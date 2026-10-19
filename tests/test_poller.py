"""Опрос СДО по пользователям и рассылка сводок по их времени."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from study_bot import poller as poller_mod
from study_bot.config import Settings
from study_bot.db import DB
from study_bot.models import Task, User
from study_bot.poller import PortalPoller, digest_due
from study_bot.sources.moodle_pages import PortalStatus
from study_bot.sources.portal import PortalAuthError
from study_bot.vault import Vault, new_key

MSK = ZoneInfo("Europe/Moscow")
SOON = datetime.now(timezone.utc) + timedelta(days=3)
ME, FRIEND = 777, 888


class FakePortal:
    """Портал, где у каждой cookie свой набор заданий."""

    def __init__(self, by_cookie: dict[str, list[str]]):
        self.by_cookie = by_cookie
        self.checked: list[tuple[str, str]] = []

    async def fetch(self, cookie):
        if cookie not in self.by_cookie:
            raise PortalAuthError("истекла")
        return [portal_task(sid) for sid in self.by_cookie[cookie]]

    async def check(self, cookie, tasks):
        self.checked += [(cookie, t.source_id) for t in tasks]
        return {t.id: PortalStatus(points=7, status="draft") for t in tasks}


class FakeNotifier:
    cookie_help = "пришли /cookie"

    def __init__(self):
        self.s = Settings(_env_file=None, bot_token="1:x", owner_id=ME)
        self.new: dict[int, list[Task]] = {}
        self.said: list[tuple[int, str]] = []
        self.digests: list[int] = []
        self.clock = datetime(2026, 10, 19, 20, 0, 30, tzinfo=MSK)

    def now(self):
        return self.clock

    async def notify_new(self, user_id, tasks):
        self.new.setdefault(user_id, []).extend(tasks)

    async def say(self, user_id, text):
        self.said.append((user_id, text))

    async def send_digest(self, user_id, full=False):
        self.digests.append(user_id)


def portal_task(sid: str) -> Task:
    return Task(source="portal", source_id=sid, title=f"КР {sid}", deadline=SOON,
                url=f"https://online-edu.mirea.ru/mod/assign/view.php?id={sid}")


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    monkeypatch.setattr(poller_mod, "USER_GAP_SECONDS", 0)


def setup(by_cookie, cookies: dict[int, str | None]):
    db, n, vault = DB(":memory:"), FakeNotifier(), Vault(new_key())
    for uid, cookie in cookies.items():
        db.add_user(uid, str(uid))
        if cookie:
            db.set_cookie(uid, vault.encrypt(cookie))
    return db, n, PortalPoller(FakePortal(by_cookie), db, n, vault)


async def test_each_user_gets_own_portal_tasks_with_ids_and_status():
    db, n, p = setup({"c-me": ["1", "2"], "c-friend": ["2", "3"]}, {ME: "c-me", FRIEND: "c-friend"})
    await p.poll_all()
    assert sorted(t.source_id for t in db.list_open(ME)) == ["1", "2"]
    assert sorted(t.source_id for t in db.list_open(FRIEND)) == ["2", "3"]
    assert all(t.id and t.points == 7 and t.status == "draft" for t in n.new[ME])
    # страницы заданий каждый смотрит со своей cookie
    assert {c for c, _ in p.portal.checked} == {"c-me", "c-friend"}


async def test_user_without_cookie_is_skipped():
    db, n, p = setup({"c-me": ["1"]}, {ME: "c-me", FRIEND: None})
    await p.poll_all()
    assert db.list_open(FRIEND) == [] and FRIEND not in n.new and n.said == []


async def test_submitted_task_is_closed_only_for_that_user():
    db, n, p = setup({"c-me": ["1", "2"], "c-friend": ["2"]}, {ME: "c-me", FRIEND: "c-friend"})
    await p.poll_all()
    p.portal.by_cookie["c-me"] = ["1"]  # я сдал «2»
    await p.poll_all()
    assert db.get_by_source(ME, "portal", "2").auto_done
    assert not db.get_by_source(FRIEND, "portal", "2").done


async def test_expired_session_warns_that_user_once_and_resets_after_new_cookie():
    db, n, p = setup({"c-friend": ["1"]}, {ME: "c-old", FRIEND: "c-friend"})
    await p.poll_all()
    await p.poll_all()
    assert n.said == [(ME, "пришли /cookie")]  # только мне и один раз
    assert db.get_user(ME).auth_warned
    p.portal.by_cookie["c-new"] = ["5"]
    db.set_cookie(ME, p.vault.encrypt("c-new"))
    assert await p.poll_user(db.get_user(ME), warn=False) == 1
    assert not db.get_user(ME).auth_warned


@pytest.mark.parametrize(
    "clock, last, due",
    [
        (datetime(2026, 10, 19, 19, 59, tzinfo=MSK), None, False),  # ещё рано
        (datetime(2026, 10, 19, 20, 0, tzinfo=MSK), None, True),
        (datetime(2026, 10, 19, 20, 0, tzinfo=MSK), date(2026, 10, 19), False),  # сегодня уже слали
        (datetime(2026, 10, 19, 23, 30, tzinfo=MSK), date(2026, 10, 18), True),  # проспали 20:00 — шлём
        (datetime(2026, 10, 20, 3, 0, tzinfo=MSK), date(2026, 10, 18), False),  # под утро — вчерашнюю не шлём
    ],
)
def test_digest_due(clock, last, due):
    user = User(id=1, name="x", digest_hour=20, digest_minute=0, last_digest=last)
    assert digest_due(user, clock, grace_hours=6) is due


async def test_digests_go_to_users_at_their_own_time_once():
    db, n, p = setup({}, {ME: None, FRIEND: None})
    db.set_digest_time(FRIEND, 21, 30)
    await p.send_due_digests()
    await p.send_due_digests()
    assert n.digests == [ME]  # 20:00 — только мне, и один раз
    n.clock = datetime(2026, 10, 19, 21, 30, 5, tzinfo=MSK)
    await p.send_due_digests()
    assert n.digests == [ME, FRIEND]
