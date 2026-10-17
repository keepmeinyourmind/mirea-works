"""SQLite-хранилище заданий.

Запросы короткие и редкие, поэтому обычный синхронный sqlite3 — без async-обёрток.
"""

import sqlite3
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
-- Какие сообщения уже отдавали в LLM (и в какой версии), чтобы не платить повторно после рестарта
CREATE TABLE IF NOT EXISTS seen_messages (
    key     TEXT PRIMARY KEY,
    version TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert(self, task: Task) -> bool:
        """Вставляет или обновляет задание. Флаг done не трогаем. True — если задание новое."""
        exists = self.conn.execute(
            "SELECT 1 FROM tasks WHERE source = ? AND source_id = ?", (task.source, task.source_id)
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO tasks (source, source_id, subject, title, deadline, url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, source_id) DO UPDATE SET
                subject = excluded.subject, title = excluded.title,
                deadline = excluded.deadline, url = excluded.url
            """,
            (task.source, task.source_id, task.subject, task.title, _dt(task.deadline), task.url),
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
        return [t for t in tasks if self.upsert(t)]

    def close_missing(self, source: str, present_ids: set[str], since: datetime) -> list[Task]:
        """Отмечает сделанными открытые задания источника, которых больше нет в выдаче
        (на портале сданное пропадает из списка). Трогаем только дедлайны внутри окна выдачи."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE source = ? AND done = 0 AND deadline >= ?", (source, _dt(since))
        ).fetchall()
        closed = [_row(r) for r in rows if r["source_id"] not in present_ids]
        for t in closed:
            self.conn.execute("UPDATE tasks SET done = 1 WHERE id = ?", (t.id,))
        self.conn.commit()
        return closed

    def list_open(self) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE done = 0 ORDER BY deadline IS NULL, deadline, id"
        ).fetchall()
        return [_row(r) for r in rows]

    def get(self, task_id: int) -> Task | None:
        r = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row(r) if r else None

    def mark_done(self, task_id: int) -> bool:
        cur = self.conn.execute("UPDATE tasks SET done = 1 WHERE id = ? AND done = 0", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0

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


def _row(r: sqlite3.Row) -> Task:
    return Task(
        id=r["id"],
        source=r["source"],
        source_id=r["source_id"],
        subject=r["subject"],
        title=r["title"],
        deadline=datetime.fromisoformat(r["deadline"]) if r["deadline"] else None,
        url=r["url"],
        done=bool(r["done"]),
    )
