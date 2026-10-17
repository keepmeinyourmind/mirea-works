from datetime import datetime, timedelta, timezone

from study_bot.db import DB
from study_bot.models import Task

MSK = timezone(timedelta(hours=3))


def make(source_id: str, title: str = "Лаба 1", deadline: datetime | None = None) -> Task:
    return Task(source="tg", source_id=source_id, title=title, deadline=deadline)


def test_upsert_does_not_duplicate():
    db = DB(":memory:")
    assert db.upsert(make("1:10:0")) is True
    assert db.upsert(make("1:10:0", title="Лаба 1 (уточнено)")) is False
    tasks = db.list_open()
    assert len(tasks) == 1
    assert tasks[0].title == "Лаба 1 (уточнено)"


def test_upsert_keeps_done_flag():
    db = DB(":memory:")
    db.upsert(make("p:1"))
    task_id = db.list_open()[0].id
    assert db.mark_done(task_id)
    db.upsert(make("p:1", title="другое"))
    assert db.list_open() == []
    assert db.mark_done(task_id) is False  # повторно не отмечается


def test_replace_group_removes_tasks_gone_after_edit():
    db = DB(":memory:")
    db.replace_group("tg", "1:10:", [make("1:10:0", "A"), make("1:10:1", "B")])
    new = db.replace_group("tg", "1:10:", [make("1:10:0", "A2")])
    assert new == []
    assert [t.title for t in db.list_open()] == ["A2"]


def test_replace_group_does_not_touch_other_messages():
    db = DB(":memory:")
    db.upsert(make("1:100:0", "чужое"))
    db.replace_group("tg", "1:10:", [])
    assert [t.title for t in db.list_open()] == ["чужое"]


def test_deadline_roundtrip_and_order():
    db = DB(":memory:")
    later = datetime(2026, 10, 25, 23, 59, tzinfo=MSK)
    sooner = datetime(2026, 10, 20, 10, 0, tzinfo=MSK)
    db.upsert(make("a", "без срока"))
    db.upsert(make("b", "позже", later))
    db.upsert(make("c", "раньше", sooner))
    tasks = db.list_open()
    assert [t.title for t in tasks] == ["раньше", "позже", "без срока"]
    assert tasks[0].deadline == sooner


def test_seen_messages_versioning():
    db = DB(":memory:")
    assert not db.is_seen("1:10", "v1")
    db.mark_seen("1:10", "v1")
    assert db.is_seen("1:10", "v1")
    assert not db.is_seen("1:10", "v2")  # сообщение отредактировали — обработать заново


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, subject TEXT,"
        " title TEXT NOT NULL, deadline TEXT, url TEXT, done INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE (source, source_id));"
        "INSERT INTO tasks (source, source_id, title) VALUES ('tg', '1', 'старое');"
    )
    old.commit()
    old.close()
    [t] = DB(path).list_open()
    assert (t.title, t.points, t.edited) == ("старое", None, False)


def test_replace_group_returns_tasks_with_ids():
    db = DB(":memory:")
    [t] = db.replace_group("tg", "1:10:", [make("1:10:0")])
    assert t.id is not None  # без id кнопка «Сделано» под уведомлением не работала бы


def test_edited_task_is_not_overwritten_by_source():
    db = DB(":memory:")
    db.upsert(Task(source="portal", source_id="7", title="КР 3", deadline=datetime(2026, 10, 25, tzinfo=MSK)))
    task_id = db.list_open()[0].id
    moved = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)
    db.update_fields(task_id, {"deadline": moved, "title": "КР 3 (перенесли)", "points": 9})
    db.upsert(Task(source="portal", source_id="7", title="КР 3", deadline=datetime(2026, 10, 25, tzinfo=MSK)))
    t = db.get(task_id)
    assert (t.title, t.deadline, t.points, t.edited) == ("КР 3 (перенесли)", moved, 9, True)


def test_removed_portal_task_stays_hidden_after_poll():
    db = DB(":memory:")
    db.upsert(Task(source="portal", source_id="7", title="КР 3"))
    task_id = db.list_open()[0].id
    assert db.remove(task_id)
    assert db.upsert(Task(source="portal", source_id="7", title="КР 3")) is False  # не «новое»
    assert db.list_open() == [] and db.list_visible() == [] and db.get(task_id) is None


def test_removed_manual_task_is_deleted():
    db = DB(":memory:")
    t = db.create_manual("Доклад", None, None, None, None)
    assert db.remove(t.id)
    assert db.get_by_source("manual", t.source_id) is None


def test_close_missing_sets_auto_done_and_manual_toggle_clears_it():
    db = DB(":memory:")
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    db.upsert(Task(source="portal", source_id="1", title="a", deadline=now + timedelta(days=2)))
    [closed] = db.close_missing("portal", set(), since=now - timedelta(days=60))
    assert db.get(closed.id).auto_done
    db.set_done(closed.id, False)
    t = db.get(closed.id)
    assert (t.done, t.auto_done) == (False, False)


def test_portal_due_for_check():
    db = DB(":memory:")
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    for sid in ("open", "graded", "fresh"):
        db.upsert(Task(source="portal", source_id=sid, title=sid, deadline=now + timedelta(days=1)))
    ids = {t.source_id: t.id for t in db.list_open()}
    db.set_done(ids["graded"], True)
    db.set_portal_status(ids["graded"], points=5, status="graded", grade=4, attempts_used=None, attempts_total=None, checked_at=now - timedelta(days=1))
    db.set_portal_status(ids["fresh"], points=5, status="none", grade=None, attempts_used=None, attempts_total=None, checked_at=now)
    due = db.portal_due_for_check(checked_before=now - timedelta(hours=3), done_since=now - timedelta(days=14))
    assert [t.source_id for t in due] == ["open"]


def test_manual_points_survive_portal_status_update():
    db = DB(":memory:")
    db.upsert(Task(source="portal", source_id="1", title="a"))
    tid = db.list_open()[0].id
    db.update_fields(tid, {"points": 9})
    db.set_portal_status(tid, points=5, status="none", grade=None, attempts_used=None, attempts_total=None, checked_at=datetime.now(timezone.utc))
    assert db.get(tid).points == 9
