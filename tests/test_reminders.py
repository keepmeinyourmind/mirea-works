"""Напоминания до срока: когда приходят, когда молчат и что не повторяются."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from study_bot.config import Settings
from study_bot.db import DB
from study_bot.models import Task
from study_bot.poller import PortalPoller
from study_bot.reminders import DAY, DRAFT, SOON, due_reminders, is_quiet
from study_bot.sources.portal import NullPortal
from study_bot.vault import Vault, new_key

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 19, 14, 0, tzinfo=MSK)
LONG_AGO = NOW - timedelta(days=5)


def task(hours_left: float, *, id=1, status=None, created=LONG_AGO, done=False) -> Task:
    return Task(source="portal", source_id=str(id), title=f"лаба {id}", id=id, done=done, status=status,
                deadline=NOW + timedelta(hours=hours_left), created_at=created)


def kinds(tasks, sent=frozenset(), now=NOW, quiet=False):
    return [(r.task.id, r.kind, r.draft) for r in due_reminders(tasks, set(sent), now, quiet=quiet)]


def sent_for(t: Task, *ks) -> set:
    return {(t.id, k, t.deadline.astimezone(timezone.utc).isoformat()) for k in ks}


@pytest.mark.parametrize(
    "hours_left, expected",
    [
        (30, []),                 # больше суток — рано
        (20, [(1, DAY, False)]),  # меньше суток
        (2, [(1, SOON, False)]),  # меньше 3 часов — сразу «скоро», без «за сутки»
        (-1, []),                 # уже просрочено — это работа сводки
    ],
)
def test_windows(hours_left, expected):
    assert kinds([task(hours_left)]) == expected


def test_done_and_no_deadline_are_ignored():
    no_deadline = Task(source="manual", source_id="x", title="x", id=2, created_at=LONG_AGO)
    assert kinds([task(2, done=True), no_deadline]) == []


def test_each_reminder_once_then_soon_after_day():
    t = task(20)
    assert kinds([t], sent_for(t, DAY)) == []
    t3 = task(2.5)
    assert kinds([t3], sent_for(t3, DAY)) == [(1, SOON, False)]  # «за сутки» было — «за 3 часа» всё равно придёт
    assert kinds([t3], sent_for(t3, DAY, SOON)) == []


def test_moved_deadline_reminds_again():
    old = task(20)
    moved = task(22)  # тот же id, новый срок
    assert kinds([moved], sent_for(old, DAY)) == [(1, DAY, False)]


def test_task_added_inside_window_is_not_reminded_for_that_window():
    # добавили за 10 часов до срока: «за сутки» не нужно, «за 3 часа» — нужно
    created = NOW - timedelta(hours=2)
    assert kinds([task(9, created=created)]) == []
    assert kinds([task(2, created=created)]) == [(1, SOON, False)]
    # добавили, когда до срока было уже 2 часа: молчим совсем
    assert kinds([task(1.5, created=NOW - timedelta(minutes=30))]) == []


def test_night_delays_day_but_not_soon():
    night = NOW.replace(hour=1)
    shift = night - NOW  # task() считает срок от NOW — сдвигаем на ночь
    day, soon = task(20), task(2)
    day.deadline += shift
    soon.deadline += shift
    assert kinds([day], now=night, quiet=True) == []
    assert kinds([soon], now=night, quiet=True) == [(1, SOON, False)]


@pytest.mark.parametrize("hour, quiet", [(22, False), (23, True), (3, True), (7, True), (8, False), (14, False)])
def test_is_quiet(hour, quiet):
    assert is_quiet(NOW.replace(hour=hour), 23, 8) is quiet


def test_draft_rides_along_or_comes_alone():
    t = task(20, status="draft")
    assert kinds([t]) == [(1, DAY, True)]  # в одном сообщении с «за сутки»
    assert kinds([t], sent_for(t, DAY, DRAFT)) == []
    # «за сутки» было ещё до того, как появился черновик — напомним о черновике отдельно
    assert kinds([t], sent_for(t, DAY)) == [(1, DRAFT, False)]
    # черновик за 2 дня до срока — пока молчим
    assert kinds([task(48, status="draft")]) == []


class FakeNotifier:
    def __init__(self, clock):
        self.s = Settings(_env_file=None, bot_token="1:x", owner_id=1)
        self.clock = clock
        self.sent: list[tuple[int, list]] = []

    def now(self):
        return self.clock

    async def send_reminders(self, user_id, items):
        self.sent.append((user_id, [(r.task.title, r.kind) for r in items]))


async def test_poller_sends_each_user_own_reminders_once():
    db = DB(":memory:")
    db.add_user(1, "я")
    db.add_user(2, "друг")
    db.upsert(Task(source="manual", source_id="a", title="моё", user_id=1, deadline=NOW + timedelta(hours=2)))
    db.upsert(Task(source="manual", source_id="b", title="друга", user_id=2, deadline=NOW + timedelta(hours=20)))
    # задания «появились» давно — иначе окно уже началось при создании
    db.conn.execute("UPDATE tasks SET created_at = '2026-10-01 00:00:00'")
    n = FakeNotifier(NOW)
    p = PortalPoller(NullPortal(), db, n, Vault(new_key()))
    await p.send_due_reminders()
    await p.send_due_reminders()
    assert sorted(n.sent) == [(1, [("моё", SOON)]), (2, [("друга", DAY)])]
    db.remove_user(2)
    assert db.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
