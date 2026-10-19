import sqlite3
from datetime import date, datetime, timedelta, timezone

from study_bot.db import DB
from study_bot.models import Task

MSK = timezone(timedelta(hours=3))
ME, FRIEND = 777, 888


def make(source_id: str, title: str = "Лаба 1", deadline: datetime | None = None, user: int = ME,
         source: str = "tg") -> Task:
    return Task(source=source, source_id=source_id, title=title, deadline=deadline, user_id=user)


def test_upsert_does_not_duplicate():
    db = DB(":memory:")
    assert db.upsert(make("1:10:0")) is True
    assert db.upsert(make("1:10:0", title="Лаба 1 (уточнено)")) is False
    tasks = db.list_open(ME)
    assert len(tasks) == 1
    assert tasks[0].title == "Лаба 1 (уточнено)"


def test_same_portal_event_for_two_users_is_two_tasks():
    # Одна контрольная на весь курс — у каждого студента своя копия со своими отметками
    db = DB(":memory:")
    assert db.upsert(make("942512", "КР 3", source="portal"))
    assert db.upsert(make("942512", "КР 3", source="portal", user=FRIEND))
    mine = db.list_open(ME)[0]
    db.mark_done(ME, mine.id)
    assert db.list_open(ME) == [] and len(db.list_open(FRIEND)) == 1


def test_users_cannot_touch_each_others_tasks():
    db = DB(":memory:")
    db.upsert(make("1"))
    tid = db.list_open(ME)[0].id
    assert db.get(FRIEND, tid) is None
    assert db.mark_done(FRIEND, tid) is False
    assert db.remove(FRIEND, tid) is False
    assert db.update_fields(FRIEND, tid, {"title": "взлом"}) is None
    db.set_done(FRIEND, tid, True)
    assert db.get(ME, tid).title == "Лаба 1" and not db.get(ME, tid).done


def test_upsert_keeps_done_flag():
    db = DB(":memory:")
    db.upsert(make("p:1"))
    task_id = db.list_open(ME)[0].id
    assert db.mark_done(ME, task_id)
    db.upsert(make("p:1", title="другое"))
    assert db.list_open(ME) == []
    assert db.mark_done(ME, task_id) is False  # повторно не отмечается


def test_replace_group_removes_tasks_gone_after_edit():
    db = DB(":memory:")
    db.replace_group(ME, "tg", "1:10:", [make("1:10:0", "A"), make("1:10:1", "B")])
    new = db.replace_group(ME, "tg", "1:10:", [make("1:10:0", "A2")])
    assert new == []
    assert [t.title for t in db.list_open(ME)] == ["A2"]


def test_replace_group_does_not_touch_other_messages_or_users():
    db = DB(":memory:")
    db.upsert(make("1:100:0", "чужое сообщение"))
    db.upsert(make("1:10:0", "у друга", user=FRIEND))
    db.replace_group(ME, "tg", "1:10:", [])
    assert [t.title for t in db.list_open(ME)] == ["чужое сообщение"]
    assert [t.title for t in db.list_open(FRIEND)] == ["у друга"]


def test_replace_group_returns_tasks_with_ids():
    db = DB(":memory:")
    [t] = db.replace_group(ME, "tg", "1:10:", [make("1:10:0")])
    assert t.id is not None and t.user_id == ME  # без id кнопка «Сделано» под уведомлением не работала бы


def test_deadline_roundtrip_and_order():
    db = DB(":memory:")
    later = datetime(2026, 10, 25, 23, 59, tzinfo=MSK)
    sooner = datetime(2026, 10, 20, 10, 0, tzinfo=MSK)
    db.upsert(make("a", "без срока"))
    db.upsert(make("b", "позже", later))
    db.upsert(make("c", "раньше", sooner))
    tasks = db.list_open(ME)
    assert [t.title for t in tasks] == ["раньше", "позже", "без срока"]
    assert tasks[0].deadline == sooner


def test_seen_messages_versioning():
    db = DB(":memory:")
    assert not db.is_seen("1:10", "v1")
    db.mark_seen("1:10", "v1")
    assert db.is_seen("1:10", "v1")
    assert not db.is_seen("1:10", "v2")  # сообщение отредактировали — обработать заново


def _old_db(path, extra_columns: str = "") -> None:
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, subject TEXT,"
        " title TEXT NOT NULL, deadline TEXT, url TEXT, done INTEGER NOT NULL DEFAULT 0,"
        f" created_at TEXT NOT NULL DEFAULT (datetime('now')){extra_columns}, UNIQUE (source, source_id));"
        "INSERT INTO tasks (source, source_id, title, done) VALUES ('tg', '1', 'старое', 0), ('portal', '9', 'сданное', 1);"
        "CREATE TABLE seen_messages (key TEXT PRIMARY KEY, version TEXT NOT NULL);"
    )
    old.commit()
    old.close()


def test_first_version_database_is_migrated_to_admin(tmp_path):
    path = tmp_path / "old.sqlite3"
    _old_db(path)
    db = DB(path, admin_id=ME)
    [t] = db.list_open(ME)
    assert (t.title, t.user_id, t.points, t.edited) == ("старое", ME, None, False)
    assert [x.title for x in db.list_visible(ME)] == ["старое", "сданное"]


def test_single_user_mini_app_database_is_migrated(tmp_path):
    # Та, что сейчас на сервере: с баллами и статусами, но без user_id
    path = tmp_path / "v3.sqlite3"
    _old_db(path, ", points REAL, status TEXT, grade REAL, attempts_used INTEGER, attempts_total INTEGER,"
                  " auto_done INTEGER NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0,"
                  " hidden INTEGER NOT NULL DEFAULT 0, checked_at TEXT")
    db = DB(path, admin_id=ME)
    assert {t.title for t in db.list_visible(ME)} == {"старое", "сданное"}
    # повторное открытие уже мигрированной базы — без ошибок и без дублей
    assert len(DB(path, admin_id=ME).list_visible(ME)) == 2


def test_users_invites_and_forget():
    db = DB(":memory:")
    code = db.create_invite()
    assert db.use_invite(code, FRIEND) is True
    assert db.use_invite(code, 999) is False  # одноразовое
    db.add_user(FRIEND, "Аня")
    db.set_digest_time(FRIEND, 21, 30)
    db.set_cookie(FRIEND, "enc")
    db.set_last_digest(FRIEND, date(2026, 10, 19))
    u = db.get_user(FRIEND)
    assert (u.name, u.digest_hour, u.digest_minute, u.cookie_enc, u.last_digest) == ("Аня", 21, 30, "enc", date(2026, 10, 19))

    db.upsert(make("1", user=FRIEND))
    db.mark_seen(f"{FRIEND}|pm:abc", "1")
    assert db.remove_user(FRIEND)
    assert db.get_user(FRIEND) is None and db.list_visible(FRIEND) == [] and not db.is_seen(f"{FRIEND}|pm:abc", "1")


def test_removed_portal_task_stays_hidden_after_poll():
    db = DB(":memory:")
    db.upsert(make("7", "КР 3", source="portal"))
    task_id = db.list_open(ME)[0].id
    assert db.remove(ME, task_id)
    assert db.upsert(make("7", "КР 3", source="portal")) is False  # не «новое»
    assert db.list_open(ME) == [] and db.list_visible(ME) == [] and db.get(ME, task_id) is None


def test_removed_manual_task_is_deleted():
    db = DB(":memory:")
    t = db.create_manual(ME, "Доклад", None, None, None, None)
    assert db.remove(ME, t.id)
    assert db.get_by_source(ME, "manual", t.source_id) is None


def test_edited_task_is_not_overwritten_by_source():
    db = DB(":memory:")
    db.upsert(make("7", "КР 3", datetime(2026, 10, 25, tzinfo=MSK), source="portal"))
    task_id = db.list_open(ME)[0].id
    moved = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)
    db.update_fields(ME, task_id, {"deadline": moved, "title": "КР 3 (перенесли)", "points": 9})
    db.upsert(make("7", "КР 3", datetime(2026, 10, 25, tzinfo=MSK), source="portal"))
    t = db.get(ME, task_id)
    assert (t.title, t.deadline, t.points, t.edited) == ("КР 3 (перенесли)", moved, 9, True)


def test_close_missing_sets_auto_done_and_manual_toggle_clears_it():
    db = DB(":memory:")
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    db.upsert(make("1", "a", now + timedelta(days=2), source="portal"))
    db.upsert(make("1", "a", now + timedelta(days=2), source="portal", user=FRIEND))
    [closed] = db.close_missing(ME, "portal", set(), since=now - timedelta(days=60))
    assert db.get(ME, closed.id).auto_done
    assert len(db.list_open(FRIEND)) == 1  # у друга не закрылось
    db.set_done(ME, closed.id, False)
    t = db.get(ME, closed.id)
    assert (t.done, t.auto_done) == (False, False)


def test_portal_due_for_check():
    db = DB(":memory:")
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    for sid in ("open", "graded", "fresh"):
        db.upsert(make(sid, sid, now + timedelta(days=1), source="portal"))
    ids = {t.source_id: t.id for t in db.list_open(ME)}
    db.set_done(ME, ids["graded"], True)
    db.set_portal_status(ids["graded"], points=5, status="graded", grade=4, attempts_used=None, attempts_total=None, checked_at=now - timedelta(days=1))
    db.set_portal_status(ids["fresh"], points=5, status="none", grade=None, attempts_used=None, attempts_total=None, checked_at=now)
    due = db.portal_due_for_check(ME, checked_before=now - timedelta(hours=3), done_since=now - timedelta(days=14))
    assert [t.source_id for t in due] == ["open"]


def test_manual_points_survive_portal_status_update():
    db = DB(":memory:")
    db.upsert(make("1", "a", source="portal"))
    tid = db.list_open(ME)[0].id
    db.update_fields(ME, tid, {"points": 9})
    db.set_portal_status(tid, points=5, status="none", grade=None, attempts_used=None, attempts_total=None, checked_at=datetime.now(timezone.utc))
    assert db.get(ME, tid).points == 9
