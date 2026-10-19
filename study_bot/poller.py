"""Опрос СДО и рассылка сводок для всех пользователей."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .db import DB
from .models import User
from .reminders import due_reminders, is_quiet
from .sources.portal import NullPortal, PortalAuthError, PortalSource
from .vault import Vault

log = logging.getLogger(__name__)

USER_GAP_SECONDS = 3  # пауза между пользователями — не дёргаем СДО пачкой


def digest_due(user: User, now: datetime, grace_hours: int) -> bool:
    """Пора ли слать сводку: наступило время пользователя, сегодня ещё не слали и не прошло grace_hours
    (Mac/сервер спал — пришлём, проснувшись, но не вчерашнюю сводку под утро)."""
    at = now.replace(hour=user.digest_hour, minute=user.digest_minute, second=0, microsecond=0)
    return user.last_digest != now.date() and at <= now < at + timedelta(hours=grace_hours)


class PortalPoller:
    def __init__(self, portal: PortalSource, db: DB, notifier, vault: Vault):
        self.portal = portal
        self.db = db
        self.notifier = notifier
        self.vault = vault

    @property
    def s(self):
        return self.notifier.s

    async def poll_all(self) -> None:
        if isinstance(self.portal, NullPortal):
            return
        for user in self.db.list_users():
            if user.cookie_enc:
                await self.poll_user(user)
                await asyncio.sleep(USER_GAP_SECONDS)

    async def poll_user(self, user: User, warn: bool = True) -> int | None:
        """Задания пользователя с портала. Возвращает их число или None, если опрос не удался."""
        cookie = self.vault.decrypt(user.cookie_enc)
        try:
            tasks = await self.portal.fetch(cookie)
        except PortalAuthError as e:
            log.warning("Портал, пользователь %s: нет доступа: %s", user.id, e)
            if warn and not user.auth_warned:
                self.db.set_auth_warned(user.id, True)
                await self.notifier.say(user.id, self.notifier.cookie_help)
            return None
        except Exception:
            log.exception("Портал, пользователь %s: ошибка опроса", user.id)
            return None
        if user.auth_warned:
            self.db.set_auth_warned(user.id, False)

        for t in tasks:
            t.user_id = user.id
        new_ids = [t.source_id for t in tasks if self.db.upsert(t)]
        now = datetime.now(timezone.utc)
        closed = self.db.close_missing(user.id, "portal", {t.source_id for t in tasks},
                                       now - timedelta(days=self.s.portal_lookback_days))
        if closed:
            log.info("Портал, пользователь %s: %d заданий сдано — закрыл", user.id, len(closed))
        await self.check_statuses(user, cookie, now)

        new = [t for sid in new_ids if (t := self.db.get_by_source(user.id, "portal", sid))]
        if new and self.s.notify_new:
            await self.notifier.notify_new(user.id, new)
        return len(tasks)

    async def check_statuses(self, user: User, cookie: str | None, now: datetime) -> None:
        """Баллы и статус со страниц заданий: новые — сразу, остальные — раз в PORTAL_STATUS_HOURS."""
        due = self.db.portal_due_for_check(user.id, now - timedelta(hours=self.s.portal_status_hours),
                                           now - timedelta(days=14))
        due = due[: self.s.portal_check_batch]  # не грузим СДО десятками страниц за раз
        if not due:
            return
        try:
            statuses = await self.portal.check(cookie, due)
        except PortalAuthError:
            return  # о протухшей сессии уже сообщает основной опрос
        except Exception:
            log.exception("Портал, пользователь %s: ошибка проверки статусов", user.id)
            return
        for task_id, st in statuses.items():
            self.db.set_portal_status(
                task_id, points=st.points, status=st.status, grade=st.grade,
                attempts_used=st.attempts_used, attempts_total=st.attempts_total, checked_at=now,
            )
        log.info("Портал, пользователь %s: обновил статус %d заданий", user.id, len(statuses))

    async def send_due_digests(self) -> None:
        """Раз в минуту: кому пора — тому сводка."""
        now = self.notifier.now()
        for user in self.db.list_users():
            if digest_due(user, now, self.s.digest_grace_hours):
                self.db.set_last_digest(user.id, now.date())  # сначала отметка — не пришлём дважды при ошибке
                try:
                    await self.notifier.send_digest(user.id)
                except Exception:
                    log.exception("Не смог отправить сводку пользователю %s", user.id)

    async def send_due_reminders(self) -> None:
        """Раз в минуту: напоминания за сутки и за 3 часа до срока, о неотправленных черновиках."""
        if not self.s.reminders:
            return
        now = self.notifier.now()
        quiet = is_quiet(now, self.s.quiet_from_hour, self.s.quiet_to_hour)
        for user in self.db.list_users():
            items = due_reminders(
                self.db.list_open(user.id), self.db.reminded(user.id), now,
                day=timedelta(hours=self.s.remind_day_hours), soon=timedelta(hours=self.s.remind_soon_hours),
                quiet=quiet,
            )
            if not items:
                continue
            self.db.mark_reminded([m for r in items for m in r.marks()])  # сначала отметка — без повторов
            try:
                await self.notifier.send_reminders(user.id, items)
            except Exception:
                log.exception("Не смог отправить напоминание пользователю %s", user.id)
