"""Цикл опроса портала: новые задания → уведомление с id и баллами, сданное → закрыто, статусы — со страниц."""

from datetime import datetime, timedelta, timezone

from study_bot.config import Settings
from study_bot.db import DB
from study_bot.main import PortalPoller
from study_bot.models import Task
from study_bot.sources.moodle_pages import PortalStatus
from study_bot.sources.portal import PortalAuthError

SOON = datetime.now(timezone.utc) + timedelta(days=3)


class FakePortal:
    def __init__(self, tasks):
        self.tasks = tasks
        self.checked = []

    async def fetch(self):
        return list(self.tasks)

    async def check(self, tasks):
        self.checked += [t.source_id for t in tasks]
        return {t.id: PortalStatus(points=7, status="draft") for t in tasks}


class FakeNotifier:
    def __init__(self):
        self.s = Settings(_env_file=None, bot_token="1:x", owner_id=1)
        self.new, self.said = [], []

    async def notify_new(self, tasks):
        self.new += tasks

    async def say(self, text):
        self.said.append(text)


def portal_task(sid: str) -> Task:
    return Task(source="portal", source_id=sid, title=f"КР {sid}", deadline=SOON, url=f"https://online-edu.mirea.ru/mod/assign/view.php?id={sid}")


async def test_new_tasks_are_checked_and_notified_with_ids():
    db, n = DB(":memory:"), FakeNotifier()
    portal = FakePortal([portal_task("1"), portal_task("2")])
    assert await PortalPoller(portal, db, n).poll() == 2
    assert sorted(portal.checked) == ["1", "2"]
    assert all(t.id and t.points == 7 and t.status == "draft" for t in n.new)  # статус уже в уведомлении


async def test_submitted_task_is_closed_and_status_not_rechecked_too_often():
    db, n = DB(":memory:"), FakeNotifier()
    portal = FakePortal([portal_task("1"), portal_task("2")])
    poller = PortalPoller(portal, db, n)
    await poller.poll()
    portal.tasks = [portal_task("1")]  # «2» сдали — пропало из выдачи
    portal.checked.clear()
    await poller.poll()
    t2 = db.get_by_source("portal", "2")
    assert t2.done and t2.auto_done
    assert portal.checked == []  # всё проверено меньше 3 часов назад


async def test_expired_session_warns_once():
    class Expired(FakePortal):
        async def fetch(self):
            raise PortalAuthError("истекла")

    db, n = DB(":memory:"), FakeNotifier()
    poller = PortalPoller(Expired([]), db, n)
    assert await poller.poll() is None
    assert await poller.poll() is None
    assert len(n.said) == 1
