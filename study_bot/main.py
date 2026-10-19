"""Точка входа: бот + планировщик + Mini App в одном asyncio-процессе.

    uv run python -m study_bot.main
"""

import asyncio
import logging

from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .bot import Notifier, build_group_router, build_router
from .config import Settings
from .db import DB
from .extract.llm import Extractor
from .extract.rules import RulesExtractor
from .poller import PortalPoller
from .sources.chat import ChatProcessor
from .sources.moodle import read_legacy_cookie
from .sources.portal import make_portal
from .vault import Vault
from .webapp import start_webapp

log = logging.getLogger("study_bot")


def build_scheduler(s: Settings, poller: PortalPoller) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=s.zone)
    # У каждого своё время сводки — раз в минуту проверяем, кому пора (см. poller.digest_due)
    scheduler.add_job(poller.send_due_digests, "interval", minutes=1, id="digest",
                      misfire_grace_time=None, coalesce=True, max_instances=1)
    scheduler.add_job(poller.send_due_reminders, "interval", minutes=1, id="reminders",
                      misfire_grace_time=None, coalesce=True, max_instances=1)
    # Пропущенные во сне опросы СДО схлопываются в один
    scheduler.add_job(poller.poll_all, "interval", minutes=s.portal_poll_minutes, next_run_time=poller.notifier.now(),
                      id="portal", misfire_grace_time=None, coalesce=True, max_instances=1)
    return scheduler


async def follow_wall_clock(scheduler: AsyncIOScheduler, every: float = 60) -> None:
    """Во сне macOS не идут таймеры процесса: задача «через 3 часа» после сна сдвинулась бы
    на время сна. Раз в минуту просим планировщик сверить расписание с настоящими часами."""
    while True:
        await asyncio.sleep(every)
        scheduler.wakeup()


def bootstrap_admin(s: Settings, db: DB, vault: Vault) -> None:
    """Админ — всегда пользователь. Cookie из однопользовательской версии переезжает в базу зашифрованной."""
    if not db.get_user(s.owner_id):
        db.add_user(s.owner_id, "админ")
    legacy = s.legacy_cookie_path
    if cookie := read_legacy_cookie(legacy):
        if not db.get_user(s.owner_id).cookie_enc:
            db.set_cookie(s.owner_id, vault.encrypt(cookie))
            log.info("Cookie СДО перенесена в базу (зашифрована)")
        legacy.unlink()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx пишет полный адрес каждого запроса — с sesskey СДО; в логе он не нужен
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # «Running job … executed successfully» каждую минуту — шум
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    s = Settings()
    vault = Vault(s.cookie_key)
    db = DB(s.db_path, admin_id=s.owner_id)
    bootstrap_admin(s, db, vault)
    if s.anthropic_api_key:
        extractor = Extractor(s.anthropic_api_key, s.llm_model, s.zone)
        log.info("Разбор сообщений: Claude (%s)", s.llm_model)
    else:
        extractor = RulesExtractor(s.zone, lambda: db.subjects(), s.subject_aliases)
        log.info("Разбор сообщений: правила (бесплатно, без LLM)")
    notifier = Notifier(s, db)

    processor = ChatProcessor(db, extractor, frozenset(s.trusted_senders))
    poller = PortalPoller(make_portal(s), db, notifier, vault)
    dp = Dispatcher()
    dp.include_router(build_group_router(notifier, processor))
    dp.include_router(build_router(notifier, db, extractor, processor, vault, poller.poll_user))

    scheduler = build_scheduler(s, poller)
    scheduler.start()
    watchdog = asyncio.create_task(follow_wall_clock(scheduler))
    log.info("Пользователей: %d", len(db.list_users()))

    runner = await start_webapp(s, db)
    try:
        await notifier.setup_menu_button()
    except Exception:
        log.exception("Не смог поставить кнопку меню с приложением")
    try:
        await dp.start_polling(notifier.bot)
    finally:
        watchdog.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
