"""Userbot: читает супергруппы от твоего аккаунта и достаёт из сообщений задания."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import anthropic
from telethon import TelegramClient, events
from telethon.tl.custom import Message

from ..config import Settings
from ..db import DB
from ..extract.llm import Extractor
from ..extract.prefilter import is_candidate
from ..models import Task

log = logging.getLogger(__name__)

OnNewTasks = Callable[[list[Task]], Awaitable[None]]


def message_link(chat_id: int, msg_id: int) -> str | None:
    # У супергрупп id вида -100XXXXXXXXXX → ссылка t.me/c/XXXXXXXXXX/<msg>
    s = str(chat_id)
    return f"https://t.me/c/{s[4:]}/{msg_id}" if s.startswith("-100") else None


class TelegramReader:
    def __init__(self, settings: Settings, db: DB, extractor: Extractor, on_new: OnNewTasks | None = None):
        self.s = settings
        self.db = db
        self.extractor = extractor
        self.on_new = on_new
        self.trusted = frozenset(settings.trusted_senders)
        self.titles: dict[int, str] = {}
        self.client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)

    async def start(self) -> None:
        # Без интерактивного ввода: сессию заранее создаёт `python -m study_bot.login`
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Userbot не авторизован. Запусти локально: python -m study_bot.login")

        for chat_id in self.s.chat_ids:
            entity = await self.client.get_entity(chat_id)
            self.titles[chat_id] = getattr(entity, "title", str(chat_id))

        chats = self.s.chat_ids
        self.client.add_event_handler(self._on_event, events.NewMessage(chats=chats))
        self.client.add_event_handler(self._on_event, events.MessageEdited(chats=chats))

        await self.backfill()
        log.info("Userbot слушает чаты: %s", ", ".join(self.titles.values()))

    async def run(self) -> None:
        await self.client.run_until_disconnected()

    async def backfill(self) -> None:
        """Догоняем историю за backfill_days — то, что пришло, пока бот не работал."""
        since = datetime.now(timezone.utc) - timedelta(days=self.s.backfill_days)
        for chat_id in self.s.chat_ids:
            async for msg in self.client.iter_messages(chat_id):
                if msg.date < since:
                    break
                # При догонялке не спамим уведомлениями — всё попадёт в сводку
                await self.process(msg, notify=False)

    async def _on_event(self, event: events.NewMessage.Event) -> None:
        await self.process(event.message, notify=self.s.notify_new)

    async def process(self, msg: Message, notify: bool) -> None:
        chat_id = msg.chat_id
        key = f"{chat_id}:{msg.id}"
        version = (msg.edit_date or msg.date).isoformat()
        if self.db.is_seen(key, version):
            return

        text = msg.message
        if not is_candidate(text, msg.sender_id, self.trusted):
            self.db.mark_seen(key, version)
            return

        try:
            extracted = await self.extractor.extract(text, msg.date, self.titles.get(chat_id))
        except anthropic.APIError:
            # Не помечаем как обработанное — повторим при следующей догонялке
            log.exception("LLM недоступна, сообщение %s пропущено", key)
            return

        link = message_link(chat_id, msg.id)
        tasks = [
            Task(source="tg", source_id=f"{key}:{i}", title=e.title, subject=e.subject, deadline=e.deadline, url=link)
            for i, e in enumerate(extracted)
        ]
        new = self.db.replace_group("tg", f"{key}:", tasks)
        self.db.mark_seen(key, version)

        if new:
            log.info("Из %s: %d новых заданий", key, len(new))
            if notify and self.on_new:
                await self.on_new(new)
