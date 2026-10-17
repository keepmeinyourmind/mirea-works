"""Точка входа: бот + планировщик в одном asyncio-процессе.

    uv run python -m study_bot.main
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .bot import COOKIE_HELP, Notifier, build_group_router, build_router
from .config import Settings
from .db import DB
from .extract.llm import Extractor
from .extract.rules import RulesExtractor
from .sources.portal import NullPortal, PortalAuthError, PortalSource, make_portal
from .sources.chat import ChatProcessor
from .webapp import start_webapp

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
        new_ids = [t.source_id for t in tasks if self.db.upsert(t)]
        if not isinstance(self.portal, NullPortal):
            now = datetime.now(timezone.utc)
            closed = self.db.close_missing("portal", {t.source_id for t in tasks}, now - timedelta(days=self.notifier.s.portal_lookback_days))
            if closed:
                log.info("Портал: %d заданий сдано — закрыл", len(closed))
            await self.check_statuses(now)
        new = [t for sid in new_ids if (t := self.db.get_by_source("portal", sid))]
        if new and self.notifier.s.notify_new:
            await self.notifier.notify_new(new)
        return len(tasks)

    async def check_statuses(self, now: datetime) -> None:
        """Баллы и статус со страниц заданий: новые — сразу, остальные — раз в PORTAL_STATUS_HOURS."""
        s = self.notifier.s
        due = self.db.portal_due_for_check(now - timedelta(hours=s.portal_status_hours), now - timedelta(days=14))
        due = due[: s.portal_check_batch]  # не грузим СДО десятками страниц за раз
        if not due or not hasattr(self.portal, "check"):
            return
        try:
            statuses = await self.portal.check(due)
        except PortalAuthError:
            return  # о протухшей сессии уже сообщает основной опрос
        except Exception:
            log.exception("Портал: ошибка проверки статусов")
            return
        for task_id, st in statuses.items():
            self.db.set_portal_status(
                task_id, points=st.points, status=st.status, grade=st.grade,
                attempts_used=st.attempts_used, attempts_total=st.attempts_total, checked_at=now,
            )
        log.info("Портал: обновил статус %d заданий", len(statuses))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings()
    db = DB(s.db_path)
    if s.anthropic_api_key:
        extractor = Extractor(s.anthropic_api_key, s.llm_model, s.zone)
        log.info("Разбор сообщений: Claude (%s)", s.llm_model)
    else:
        extractor = RulesExtractor(s.zone, db.subjects, s.subject_aliases)
        log.info("Разбор сообщений: правила (бесплатно, без LLM)")
    notifier = Notifier(s, db)

    processor = ChatProcessor(db, extractor, frozenset(s.trusted_senders))
    poller = PortalPoller(make_portal(s), db, notifier)
    dp = Dispatcher()
    dp.include_router(build_router(notifier, db, extractor, processor, poller.poll))
    dp.include_router(build_group_router(notifier, processor))

    scheduler = AsyncIOScheduler(timezone=s.zone)
    scheduler.add_job(notifier.send_digest, CronTrigger(hour=s.digest_hour, minute=s.digest_minute, timezone=s.zone))
    scheduler.add_job(poller.poll, "interval", minutes=s.portal_poll_minutes, next_run_time=notifier.now())
    scheduler.start()
    log.info("Сводка каждый день в %02d:%02d (%s)", s.digest_hour, s.digest_minute, s.tz)

    runner = await start_webapp(s, db)
    try:
        await notifier.setup_menu_button()
    except Exception:
        log.exception("Не смог поставить кнопку меню с приложением")
    try:
        await dp.start_polling(notifier.bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
