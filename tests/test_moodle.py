"""Moodle-адаптер на подменённом HTTP (httpx.MockTransport) — без сети и настоящей cookie."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from study_bot.db import DB
from study_bot.models import Task
from study_bot.sources.moodle import PAGE, MoodleSessionPortal, clean_subject, event_to_task, save_cookie
from study_bot.sources.portal import PortalAuthError

# Форма события как у online-edu.mirea.ru (поля урезаны до используемых)
EVENT = {
    "id": 1321758,
    "name": "Мини-хакатон №1 - срок сдачи",
    "activityname": "Мини-хакатон №1",
    "timesort": 1791665880,
    "course": {"fullname": "Проектная деятельность_Зачет (часть 1/2) [I.26-27]"},
    "url": "https://online-edu.mirea.ru/mod/assign/view.php?id=949324",
}


MY_PAGE = '<script>M.cfg = {"wwwroot":"https://online-edu.mirea.ru","sesskey":"SK123"};</script>'


def portal(tmp_path, ajax, cookie="abc", my_page=None) -> MoodleSessionPortal:
    path = tmp_path / "moodle_session.txt"
    if cookie:
        save_cookie(path, cookie)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == f"MoodleSession={cookie}"
        if request.url.path == "/my/":
            return my_page or httpx.Response(200, text=MY_PAGE)
        assert request.url.path == "/lib/ajax/service.php"
        assert request.url.params["sesskey"] == "SK123"
        [call] = json.loads(request.content)
        return httpx.Response(200, json=[ajax(call["args"])])

    return MoodleSessionPortal("https://online-edu.mirea.ru/", path, lookback_days=60, transport=httpx.MockTransport(handler))


def test_event_to_task():
    t = event_to_task(EVENT)
    assert (t.source, t.source_id) == ("portal", "1321758")
    assert t.title == "Мини-хакатон №1"
    assert t.subject == "Проектная деятельность"
    assert t.deadline == datetime.fromtimestamp(1791665880, tz=timezone.utc)


@pytest.mark.parametrize(
    "fullname, expected",
    [
        ("Фронтенд и бэкенд разработка_Экзамен (часть 1/2) [I.26-27]", "Фронтенд и бэкенд разработка"),
        ("Физкультура", "Физкультура"),
        (None, None),
        ("", None),
    ],
)
def test_clean_subject(fullname, expected):
    assert clean_subject(fullname) == expected


async def test_fetch_paginates(tmp_path):
    calls = []

    def ajax(args):
        calls.append(args)
        if "aftereventid" not in args:
            events = [{**EVENT, "id": i} for i in range(PAGE)]
            return {"error": False, "data": {"events": events, "firstid": 0, "lastid": PAGE - 1}}
        return {"error": False, "data": {"events": [{**EVENT, "id": 999}], "firstid": 999, "lastid": 999}}

    tasks = await portal(tmp_path, ajax).fetch()
    assert len(tasks) == PAGE + 1
    assert calls[1]["aftereventid"] == PAGE - 1


async def test_redirect_to_login_is_auth_error(tmp_path):
    login = httpx.Response(303, headers={"Location": "https://online-edu.mirea.ru/login/index.php"})
    with pytest.raises(PortalAuthError):
        await portal(tmp_path, lambda a: {}, my_page=login).fetch()


async def test_missing_cookie_is_auth_error(tmp_path):
    with pytest.raises(PortalAuthError):
        await portal(tmp_path, lambda a: {}, cookie="").fetch()


async def test_expired_session_in_ajax_is_auth_error(tmp_path):
    def ajax(args):
        return {"error": True, "exception": {"errorcode": "servicerequireslogin", "message": "login"}}

    with pytest.raises(PortalAuthError):
        await portal(tmp_path, ajax).fetch()


async def test_other_moodle_error_is_not_auth(tmp_path):
    def ajax(args):
        return {"error": True, "exception": {"errorcode": "invalidparameter"}}

    with pytest.raises(RuntimeError):
        await portal(tmp_path, ajax).fetch()


def test_cookie_file_is_private(tmp_path):
    path = tmp_path / "c.txt"
    save_cookie(path, "  value\n")
    assert path.read_text() == "value"
    assert path.stat().st_mode & 0o077 == 0


def test_close_missing_marks_submitted_portal_tasks_done():
    db = DB(":memory:")
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    for sid, days in [("1", 3), ("2", 5), ("old", -90)]:
        db.upsert(Task(source="portal", source_id=sid, title=sid, deadline=now + timedelta(days=days)))
    db.upsert(Task(source="tg", source_id="x", title="из чата", deadline=now))

    closed = db.close_missing("portal", {"1"}, since=now - timedelta(days=60))
    assert [t.source_id for t in closed] == ["2"]  # "old" вне окна выдачи — не трогаем
    assert sorted(t.source_id for t in db.list_open()) == ["1", "old", "x"]
