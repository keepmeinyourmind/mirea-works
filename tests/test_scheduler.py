"""Расписание: сводку, проспанную Mac'ом, присылаем после пробуждения; пропущенные опросы схлопываем."""

import asyncio

from study_bot.config import Settings
from study_bot.main import build_scheduler, follow_wall_clock


class Stub:
    def __init__(self):
        self.s = Settings(_env_file=None, bot_token="1:x", owner_id=1)

    def now(self):
        from datetime import datetime
        return datetime.now(self.s.zone)

    async def send_digest(self):
        pass

    async def poll(self):
        pass


def test_digest_survives_sleep_and_polls_coalesce():
    n = Stub()
    sch = build_scheduler(n.s, n, n)
    digest, portal = sch.get_job("digest"), sch.get_job("portal")
    assert digest.misfire_grace_time == 6 * 3600 and digest.coalesce
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
