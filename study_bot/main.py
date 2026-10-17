"""Точка входа: userbot + бот + планировщик в одном asyncio-процессе.

    uv run python -m study_bot.main
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .bot import COOKIE_HELP, Notifier, build_router
from .config import Settings
from .db import DB
from .extract.llm import Extractor
from .sources.portal import NullPortal, PortalAuthError, PortalSource, make_portal
from .sources.telegram import TelegramReader

log = logging.getLogger("study_bot")


class PortalPoller:
    def __init__(self, portal: PortalSource, db: DB, notifier: Notifier):
        self.portal = portal
        self.db = db
        self.notifier = notifier
        self.auth_warned = False  # чтобы не слать «пришли cookie» при каждом опросе

    async def poll(self, warn: bool = True) -> int | None:
        """Возвращает число заданий на портале или None, если опрос не удался."""
        try:
            tasks = await self.portal.fetch()
        except PortalAuthError as e:
            log.warning("Портал: нет доступа: %s", e)
            if warn and not self.auth_warned:
                self.auth_warned = True
                await self.notifier.say(COOKIE_HELP)
            return None
        except Exception:
            log.exception("Портал: ошибка опроса")
            return None
        self.auth_warned = False
        new = [t for t in tasks if self.db.upsert(t)]
        if not isinstance(self.portal, NullPortal):
            since = datetime.now(timezone.utc) - timedelta(days=self.notifier.s.portal_lookback_days)
            closed = self.db.close_missing("portal", {t.source_id for t in tasks}, since)
            if closed:
                log.info("Портал: %d заданий сдано — закрыл", len(closed))
        if new and self.notifier.s.notify_new:
            await self.notifier.notify_new(new)
        return len(tasks)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings()
    db = DB(s.db_path)
    extractor = Extractor(s.anthropic_api_key, s.llm_model, s.zone)
    notifier = Notifier(s, db)

    poller = PortalPoller(make_portal(s), db, notifier)
    dp = Dispatcher()
    dp.include_router(build_router(notifier, db, extractor, poller.poll))

    reader = TelegramReader(s, db, extractor, on_new=notifier.notify_new)
    await reader.start()

    scheduler = AsyncIOScheduler(timezone=s.zone)
    scheduler.add_job(notifier.send_digest, CronTrigger(hour=s.digest_hour, minute=s.digest_minute, timezone=s.zone))
    scheduler.add_job(poller.poll, "interval", minutes=s.portal_poll_minutes, next_run_time=notifier.now())
    scheduler.start()
    log.info("Сводка каждый день в %02d:%02d (%s)", s.digest_hour, s.digest_minute, s.tz)

    await asyncio.gather(dp.start_polling(notifier.bot), reader.run())


if __name__ == "__main__":
    asyncio.run(main())
