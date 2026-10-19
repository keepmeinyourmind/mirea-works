"""SQLite-хранилище: пользователи, приглашения, задания.

Запросы короткие и редкие, поэтому обычный синхронный sqlite3 — без async-обёрток.
Все операции с заданиями — в рамках одного пользователя (user_id = Telegram id).
"""

import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from .models import Task, User

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    digest_hour   INTEGER NOT NULL DEFAULT 20,
    digest_minute INTEGER NOT NULL DEFAULT 0,
    cookie_enc    TEXT,
    auth_warned   INTEGER NOT NULL DEFAULT 0,
    last_digest   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    used_by    INTEGER
);
-- Какие сообщения уже разбирали (и в какой версии), чтобы не разбирать повторно после рестарта
CREATE TABLE IF NOT EXISTS seen_messages (
    key     TEXT PRIMARY KEY,
    version TEXT NOT NULL
);
"""

TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL,
    source         TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    subject        TEXT,
    title          TEXT NOT NULL,
    deadline       TEXT,
    url            TEXT,
    done           INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    points         REAL,
    status         TEXT,
    grade          REAL,
    attempts_used  INTEGER,
    attempts_total INTEGER,
    auto_done      INTEGER NOT NULL DEFAULT 0,
    edited         INTEGER NOT NULL DEFAULT 0,
    -- Удалённое задание портала не стираем, а прячем — иначе следующий опрос вернёт его
    hidden         INTEGER NOT NULL DEFAULT 0,
    checked_at     TEXT,
    -- У двух студентов одного курса задания СДО приходят с одинаковыми id — уникальность внутри пользователя
    UNIQUE (user_id, source, source_id)
);
CREATE INDEX IF NOT EXISTS tasks_user ON tasks (user_id, done, hidden);
"""

# Колонки, добавленные в однопользовательской версии (до user_id) — для миграции старых баз
OLD_COLUMNS = {
    "points": "REAL", "status": "TEXT", "grade": "REAL", "attempts_used": "INTEGER", "attempts_total": "INTEGER",
    "auto_done": "INTEGER NOT NULL DEFAULT 0", "edited": "INTEGER NOT NULL DEFAULT 0",
    "hidden": "INTEGER NOT NULL DEFAULT 0", "checked_at": "TEXT",
}

# Поля, которые можно менять из приложения
EDITABLE = {"title", "subject", "deadline", "points", "url"}


class DB:
    def __init__(self, path: Path | str, admin_id: int | None = None):
        """admin_id — кому отдать задания из старой однопользовательской базы."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: aiohttp и планировщик живут в одном потоке, но sqlite3 этого не знает
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate_tasks(admin_id)
        self.conn.executescript(TASKS)
        self.conn.commit()

    def _migrate_tasks(self, admin_id: int | None) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        if not cols or "user_id" in cols:
            return
        if admin_id is None:
            raise RuntimeError("Старая база без пользователей: нужен admin_id, чтобы перенести задания")
        # Старая таблица: сперва догоняем недостающие колонки, потом пересобираем с user_id
        for col, decl in OLD_COLUMNS.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
        self.conn.execute("ALTER TABLE tasks RENAME TO tasks_old")
        self.conn.executescript(TASKS)
        keep = "source, source_id, subject, title, deadline, url, done, created_at, " + ", ".join(OLD_COLUMNS)
        self.conn.execute(f"INSERT INTO tasks (user_id, {keep}) SELECT ?, {keep} FROM tasks_old", (admin_id,))
        self.conn.execute("DROP TABLE tasks_old")
        self.conn.commit()

    # ---------- пользователи и приглашения ----------

    def add_user(self, user_id: int, name: str) -> User:
        self.conn.execute(
            "INSERT INTO users (id, name) VALUES (?, ?) ON CONFLICT (id) DO UPDATE SET name = excluded.name",
            (user_id, name),
        )
        self.conn.commit()
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> User | None:
        r = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(r) if r else None

    def list_users(self) -> list[User]:
        return [_user(r) for r in self.conn.execute("SELECT * FROM users ORDER BY created_at")]

    def remove_user(self, user_id: int) -> bool:
        """Удаляет пользователя и все его данные (задания, cookie)."""
        self.conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
        self.conn.execute("DELETE FROM seen_messages WHERE key LIKE ? || '|%'", (str(user_id),))
        cur = self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def set_cookie(self, user_id: int, cookie_enc: str | None) -> None:
        self.conn.execute("UPDATE users SET cookie_enc = ?, auth_warned = 0 WHERE id = ?", (cookie_enc, user_id))
        self.conn.commit()

    def set_auth_warned(self, user_id: int, warned: bool) -> None:
        self.conn.execute("UPDATE users SET auth_warned = ? WHERE id = ?", (int(warned), user_id))
        self.conn.commit()

    def set_digest_time(self, user_id: int, hour: int, minute: int) -> None:
        self.conn.execute("UPDATE users SET digest_hour = ?, digest_minute = ? WHERE id = ?", (hour, minute, user_id))
        self.conn.commit()

    def set_last_digest(self, user_id: int, day: date) -> None:
        self.conn.execute("UPDATE users SET last_digest = ? WHERE id = ?", (day.isoformat(), user_id))
        self.conn.commit()

    def create_invite(self) -> str:
        code = uuid.uuid4().hex[:16]
        self.conn.execute("INSERT INTO invites (code) VALUES (?)", (code,))
        self.conn.commit()
        return code

    def use_invite(self, code: str, user_id: int) -> bool:
        """Одноразовое приглашение: True, если код был свободен и теперь занят этим пользователем."""
        cur = self.conn.execute("UPDATE invites SET used_by = ? WHERE code = ? AND used_by IS NULL", (user_id, code))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- задания ----------

    def upsert(self, task: Task) -> bool:
        """Вставляет или обновляет задание пользователя task.user_id. done/hidden не трогаем; поля,
        исправленные вручную (edited), источник больше не перезаписывает. True — если задание новое."""
        key = (task.user_id, task.source, task.source_id)
        exists = self.conn.execute(
            "SELECT 1 FROM tasks WHERE user_id = ? AND source = ? AND source_id = ?", key
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO tasks (user_id, source, source_id, subject, title, deadline, url, points)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, source, source_id) DO UPDATE SET
                subject  = CASE WHEN edited THEN subject  ELSE excluded.subject  END,
                title    = CASE WHEN edited THEN title    ELSE excluded.title    END,
                deadline = CASE WHEN edited THEN deadline ELSE excluded.deadline END,
                url      = CASE WHEN edited THEN url      ELSE excluded.url      END,
                points   = CASE WHEN edited THEN points   ELSE COALESCE(excluded.points, points) END
            """,
            (*key, task.subject, task.title, _dt(task.deadline), task.url, task.points),
        )
        self.conn.commit()
        return exists is None

    def replace_group(self, user_id: int, source: str, prefix: str, tasks: list[Task]) -> list[Task]:
        """Заменяет все задания пользователя с source_id, начинающимся на prefix (одно сообщение может
        дать несколько заданий; после редактирования их набор меняется). Возвращает новые задания с id."""
        keep = {t.source_id for t in tasks}
        rows = self.conn.execute(
            "SELECT source_id FROM tasks WHERE user_id = ? AND source = ? AND source_id LIKE ? || '%'",
            (user_id, source, prefix),
        ).fetchall()
        for r in rows:
            if r["source_id"] not in keep:
                self.conn.execute(
                    "DELETE FROM tasks WHERE user_id = ? AND source = ? AND source_id = ?",
                    (user_id, source, r["source_id"]),
                )
        self.conn.commit()
        for t in tasks:
            t.user_id = user_id
        new = [t for t in tasks if self.upsert(t)]
        return [self.get_by_source(user_id, t.source, t.source_id) for t in new]

    def close_missing(self, user_id: int, source: str, present_ids: set[str], since: datetime) -> list[Task]:
        """Отмечает сделанными открытые задания источника, которых больше нет в выдаче
        (на портале сданное пропадает из списка). Трогаем только дедлайны внутри окна выдачи."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND source = ? AND done = 0 AND deadline >= ?",
            (user_id, source, _dt(since)),
        ).fetchall()
        closed = [_row(r) for r in rows if r["source_id"] not in present_ids]
        for t in closed:
            self.conn.execute("UPDATE tasks SET done = 1, auto_done = 1 WHERE id = ?", (t.id,))
        self.conn.commit()
        return closed

    def subjects(self, user_id: int | None = None) -> list[str]:
        """Названия предметов — словарь для разбора правилами и подсказки в приложении."""
        if user_id is None:
            rows = self.conn.execute("SELECT DISTINCT subject FROM tasks WHERE subject IS NOT NULL")
        else:
            rows = self.conn.execute(
                "SELECT DISTINCT subject FROM tasks WHERE user_id = ? AND subject IS NOT NULL", (user_id,)
            )
        return [r["subject"] for r in rows]

    def list_open(self, user_id: int) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND done = 0 AND hidden = 0 ORDER BY deadline IS NULL, deadline, id",
            (user_id,),
        ).fetchall()
        return [_row(r) for r in rows]

    def list_visible(self, user_id: int, done_since: datetime | None = None) -> list[Task]:
        """Всё, что показываем в приложении: открытые + сделанные (с дедлайном не раньше done_since)."""
        rows = self.conn.execute(
            """SELECT * FROM tasks WHERE user_id = ? AND hidden = 0
                 AND (done = 0 OR ? IS NULL OR deadline IS NULL OR deadline >= ?)
               ORDER BY deadline IS NULL, deadline, id""",
            (user_id, _dt(done_since), _dt(done_since)),
        ).fetchall()
        return [_row(r) for r in rows]

    def get(self, user_id: int, task_id: int) -> Task | None:
        r = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id = ? AND hidden = 0", (task_id, user_id)
        ).fetchone()
        return _row(r) if r else None

    def get_by_source(self, user_id: int, source: str, source_id: str) -> Task | None:
        r = self.conn.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND source = ? AND source_id = ?", (user_id, source, source_id)
        ).fetchone()
        return _row(r) if r else None

    def mark_done(self, user_id: int, task_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET done = 1 WHERE id = ? AND user_id = ? AND done = 0", (task_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_done(self, user_id: int, task_id: int, done: bool) -> None:
        # Ручная отметка снимает пометку «закрыто автоматически»
        self.conn.execute(
            "UPDATE tasks SET done = ?, auto_done = 0 WHERE id = ? AND user_id = ?", (int(done), task_id, user_id)
        )
        self.conn.commit()

    def create_manual(self, user_id: int, title: str, subject: str | None, deadline: datetime | None,
                      points: float | None, url: str | None) -> Task:
        sid = uuid.uuid4().hex
        self.conn.execute(
            """INSERT INTO tasks (user_id, source, source_id, subject, title, deadline, url, points)
               VALUES (?, 'manual', ?, ?, ?, ?, ?, ?)""",
            (user_id, sid, subject, title, _dt(deadline), url, points),
        )
        self.conn.commit()
        return self.get_by_source(user_id, "manual", sid)

    def update_fields(self, user_id: int, task_id: int, fields: dict) -> Task | None:
        """Правка из приложения. Помечает задание edited, чтобы источник не затёр правки."""
        fields = {k: v for k, v in fields.items() if k in EDITABLE}
        if fields:
            if "deadline" in fields:
                fields["deadline"] = _dt(fields["deadline"])
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(
                f"UPDATE tasks SET {sets}, edited = 1 WHERE id = ? AND user_id = ? AND hidden = 0",
                (*fields.values(), task_id, user_id),
            )
            self.conn.commit()
        return self.get(user_id, task_id)

    def remove(self, user_id: int, task_id: int) -> bool:
        r = self.conn.execute(
            "SELECT source FROM tasks WHERE id = ? AND user_id = ? AND hidden = 0", (task_id, user_id)
        ).fetchone()
        if not r:
            return False
        if r["source"] == "portal":
            self.conn.execute("UPDATE tasks SET hidden = 1 WHERE id = ?", (task_id,))
        else:
            self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return True

    def portal_due_for_check(self, user_id: int, checked_before: datetime, done_since: datetime) -> list[Task]:
        """Задания портала, чей статус пора обновить: открытые и недавно сданные, но ещё не оценённые."""
        rows = self.conn.execute(
            """SELECT * FROM tasks WHERE user_id = ? AND source = 'portal' AND hidden = 0
                 AND (checked_at IS NULL OR checked_at < ?)
                 AND (done = 0 OR (COALESCE(status, '') != 'graded' AND deadline >= ?))""",
            (user_id, _dt(checked_before), _dt(done_since)),
        ).fetchall()
        return [_row(r) for r in rows]

    def set_portal_status(self, task_id: int, *, points: float | None, status: str | None, grade: float | None,
                          attempts_used: int | None, attempts_total: int | None, checked_at: datetime) -> None:
        self.conn.execute(
            """UPDATE tasks SET points = CASE WHEN edited AND points IS NOT NULL THEN points ELSE COALESCE(?, points) END,
                   status = ?, grade = ?, attempts_used = ?, attempts_total = ?, checked_at = ? WHERE id = ?""",
            (points, status, grade, attempts_used, attempts_total, _dt(checked_at), task_id),
        )
        self.conn.commit()

    # ---------- разобранные сообщения ----------

    def is_seen(self, key: str, version: str) -> bool:
        r = self.conn.execute("SELECT version FROM seen_messages WHERE key = ?", (key,)).fetchone()
        return r is not None and r["version"] == version

    def mark_seen(self, key: str, version: str) -> None:
        self.conn.execute(
            "INSERT INTO seen_messages (key, version) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET version = excluded.version",
            (key, version),
        )
        self.conn.commit()


def _dt(d: datetime | None) -> str | None:
    # Храним в UTC: тогда ORDER BY по строке совпадает с порядком по времени
    return d.astimezone(timezone.utc).isoformat() if d else None


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _user(r: sqlite3.Row) -> User:
    return User(
        id=r["id"], name=r["name"], digest_hour=r["digest_hour"], digest_minute=r["digest_minute"],
        cookie_enc=r["cookie_enc"], auth_warned=bool(r["auth_warned"]),
        last_digest=date.fromisoformat(r["last_digest"]) if r["last_digest"] else None,
    )


def _row(r: sqlite3.Row) -> Task:
    return Task(
        id=r["id"],
        user_id=r["user_id"],
        source=r["source"],
        source_id=r["source_id"],
        subject=r["subject"],
        title=r["title"],
        deadline=_parse(r["deadline"]),
        url=r["url"],
        done=bool(r["done"]),
        points=r["points"],
        status=r["status"],
        grade=r["grade"],
        attempts_used=r["attempts_used"],
        attempts_total=r["attempts_total"],
        auto_done=bool(r["auto_done"]),
        edited=bool(r["edited"]),
        checked_at=_parse(r["checked_at"]),
    )
