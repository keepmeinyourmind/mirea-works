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
