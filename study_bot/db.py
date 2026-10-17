"""SQLite-хранилище заданий.

Запросы короткие и редкие, поэтому обычный синхронный sqlite3 — без async-обёрток.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    subject    TEXT,
    title      TEXT NOT NULL,
    deadline   TEXT,
    url        TEXT,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_id)
);
-- Какие сообщения уже разбирали (и в какой версии), чтобы не разбирать повторно после рестарта
CREATE TABLE IF NOT EXISTS seen_messages (
    key     TEXT PRIMARY KEY,
    version TEXT NOT NULL
);
"""

# Колонки, добавленные после первой версии: старая база дополняется при запуске
MIGRATIONS = {
    "points": "REAL",
    "status": "TEXT",
    "grade": "REAL",
    "attempts_used": "INTEGER",
    "attempts_total": "INTEGER",
    "auto_done": "INTEGER NOT NULL DEFAULT 0",
    "edited": "INTEGER NOT NULL DEFAULT 0",
    # Удалённое задание портала не стираем, а прячем — иначе следующий опрос вернёт его
    "hidden": "INTEGER NOT NULL DEFAULT 0",
    "checked_at": "TEXT",
}

# Поля, которые можно менять из приложения
EDITABLE = {"title", "subject", "deadline", "points", "url"}


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: aiohttp и планировщик живут в одном потоке, но sqlite3 этого не знает
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        for col, decl in MIGRATIONS.items():
            if col not in have:
                self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
        self.conn.commit()

    def upsert(self, task: Task) -> bool:
        """Вставляет или обновляет задание. done/hidden не трогаем; поля, исправленные вручную
        (edited), источник больше не перезаписывает. True — если задание новое."""
        exists = self.conn.execute(
            "SELECT 1 FROM tasks WHERE source = ? AND source_id = ?", (task.source, task.source_id)
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO tasks (source, source_id, subject, title, deadline, url, points)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, source_id) DO UPDATE SET
                subject  = CASE WHEN edited THEN subject  ELSE excluded.subject  END,
                title    = CASE WHEN edited THEN title    ELSE excluded.title    END,
                deadline = CASE WHEN edited THEN deadline ELSE excluded.deadline END,
                url      = CASE WHEN edited THEN url      ELSE excluded.url      END,
                points   = CASE WHEN edited THEN points   ELSE COALESCE(excluded.points, points) END
            """,
            (task.source, task.source_id, task.subject, task.title, _dt(task.deadline), task.url, task.points),
        )
        self.conn.commit()
        return exists is None

    def replace_group(self, source: str, prefix: str, tasks: list[Task]) -> list[Task]:
        """Заменяет все задания с source_id, начинающимся на prefix (одно сообщение может дать
        несколько заданий; после редактирования их набор меняется). Возвращает новые задания."""
        keep = {t.source_id for t in tasks}
        rows = self.conn.execute(
            "SELECT source_id FROM tasks WHERE source = ? AND source_id LIKE ? || '%'", (source, prefix)
        ).fetchall()
        for r in rows:
            if r["source_id"] not in keep:
                self.conn.execute(
                    "DELETE FROM tasks WHERE source = ? AND source_id = ?", (source, r["source_id"])
                )
        self.conn.commit()
        new = [t for t in tasks if self.upsert(t)]
        # Возвращаем с id из базы — по ним кнопки «Сделано» и ссылки в приложение
        return [self.get_by_source(t.source, t.source_id) for t in new]

    def close_missing(self, source: str, present_ids: set[str], since: datetime) -> list[Task]:
        """Отмечает сделанными открытые задания источника, которых больше нет в выдаче
        (на портале сданное пропадает из списка). Трогаем только дедлайны внутри окна выдачи."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE source = ? AND done = 0 AND deadline >= ?", (source, _dt(since))
        ).fetchall()
        closed = [_row(r) for r in rows if r["source_id"] not in present_ids]
        for t in closed:
            self.conn.execute("UPDATE tasks SET done = 1, auto_done = 1 WHERE id = ?", (t.id,))
        self.conn.commit()
        return closed

    def subjects(self, source: str | None = "portal") -> list[str]:
        """Названия предметов — словарь для разбора правилами (source=None — из всех заданий)."""
        if source is None:
            rows = self.conn.execute("SELECT DISTINCT subject FROM tasks WHERE subject IS NOT NULL").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT DISTINCT subject FROM tasks WHERE source = ? AND subject IS NOT NULL", (source,)
            ).fetchall()
        return [r["subject"] for r in rows]

    def list_open(self) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE done = 0 AND hidden = 0 ORDER BY deadline IS NULL, deadline, id"
        ).fetchall()
        return [_row(r) for r in rows]

    def list_visible(self, done_since: datetime | None = None) -> list[Task]:
        """Всё, что показываем в приложении: открытые + сделанные (с дедлайном не раньше done_since)."""
        rows = self.conn.execute(
            """SELECT * FROM tasks WHERE hidden = 0 AND (done = 0 OR ? IS NULL OR deadline IS NULL OR deadline >= ?)
               ORDER BY deadline IS NULL, deadline, id""",
            (_dt(done_since), _dt(done_since)),
        ).fetchall()
        return [_row(r) for r in rows]

    def get(self, task_id: int) -> Task | None:
        r = self.conn.execute("SELECT * FROM tasks WHERE id = ? AND hidden = 0", (task_id,)).fetchone()
        return _row(r) if r else None

    def get_by_source(self, source: str, source_id: str) -> Task | None:
        r = self.conn.execute("SELECT * FROM tasks WHERE source = ? AND source_id = ?", (source, source_id)).fetchone()
        return _row(r) if r else None

    def mark_done(self, task_id: int) -> bool:
        cur = self.conn.execute("UPDATE tasks SET done = 1 WHERE id = ? AND done = 0", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def set_done(self, task_id: int, done: bool) -> None:
        # Ручная отметка снимает пометку «закрыто автоматически»
        self.conn.execute("UPDATE tasks SET done = ?, auto_done = 0 WHERE id = ?", (int(done), task_id))
        self.conn.commit()

    def create_manual(self, title: str, subject: str | None, deadline: datetime | None,
                      points: float | None, url: str | None) -> Task:
        sid = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO tasks (source, source_id, subject, title, deadline, url, points) VALUES ('manual', ?, ?, ?, ?, ?, ?)",
            (sid, subject, title, _dt(deadline), url, points),
        )
        self.conn.commit()
        return self.get_by_source("manual", sid)

    def update_fields(self, task_id: int, fields: dict) -> Task | None:
        """Правка из приложения. Помечает задание edited, чтобы источник не затёр правки."""
        fields = {k: v for k, v in fields.items() if k in EDITABLE}
        if fields:
            if "deadline" in fields:
                fields["deadline"] = _dt(fields["deadline"])
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(f"UPDATE tasks SET {sets}, edited = 1 WHERE id = ? AND hidden = 0", (*fields.values(), task_id))
            self.conn.commit()
        return self.get(task_id)

    def remove(self, task_id: int) -> bool:
        r = self.conn.execute("SELECT source FROM tasks WHERE id = ? AND hidden = 0", (task_id,)).fetchone()
        if not r:
            return False
        if r["source"] == "portal":
            self.conn.execute("UPDATE tasks SET hidden = 1 WHERE id = ?", (task_id,))
        else:
            self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return True

    def portal_due_for_check(self, checked_before: datetime, done_since: datetime) -> list[Task]:
        """Задания портала, чей статус пора обновить: открытые и недавно сданные, но ещё не оценённые."""
        rows = self.conn.execute(
            """SELECT * FROM tasks WHERE source = 'portal' AND hidden = 0
                 AND (checked_at IS NULL OR checked_at < ?)
                 AND (done = 0 OR (COALESCE(status, '') != 'graded' AND deadline >= ?))""",
            (_dt(checked_before), _dt(done_since)),
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


def _row(r: sqlite3.Row) -> Task:
    return Task(
        id=r["id"],
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
