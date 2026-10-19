"""Расписание: сводки проверяются раз в минуту (время у каждого своё), пропущенные опросы схлопываются."""

import asyncio

from study_bot.config import Settings
from study_bot.main import build_scheduler, follow_wall_clock


class StubPoller:
    def __init__(self):
        self.s = Settings(_env_file=None, bot_token="1:x", owner_id=1)
        self.notifier = self

    def now(self):
        from datetime import datetime
        return datetime.now(self.s.zone)

    async def send_due_digests(self):
        pass

    async def poll_all(self):
        pass


def test_digest_checked_every_minute_and_polls_coalesce():
    p = StubPoller()
    sch = build_scheduler(p.s, p)
    digest, portal = sch.get_job("digest"), sch.get_job("portal")
    assert digest.trigger.interval.total_seconds() == 60 and digest.coalesce and digest.max_instances == 1
    assert portal.misfire_grace_time is None and portal.coalesce and portal.max_instances == 1


async def test_watchdog_rechecks_schedule():
    calls = []

    class FakeScheduler:
        def wakeup(self):
            calls.append(1)

    task = asyncio.create_task(follow_wall_clock(FakeScheduler(), every=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    assert len(calls) >= 2
