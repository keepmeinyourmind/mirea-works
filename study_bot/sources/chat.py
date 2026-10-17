"""Сообщения из Telegram → задания. Без входа в аккаунт: всё приходит обычному боту (Bot API):
  * ты пересылаешь боту сообщение из группы или пишешь текст сам;
  * бота добавили в учебную группу (privacy mode выключен) — он читает её сам.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

import anthropic

from ..db import DB
from ..extract.llm import Extractor
from ..extract.prefilter import is_candidate
from ..models import Task

log = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    key: str  # по нему дедуп и замена заданий после редактирования
    version: str  # меняется при редактировании — тогда разбираем заново
    text: str | None
    sent_at: datetime  # для пересланных — дата оригинала: «к пятнице» считаем от неё
    source: str = "tg"
    sender_id: int | None = None
    chat_title: str | None = None
    url: str | None = None
    # Ты сам прислал боту — значит, точно хочешь разобрать: фильтр не применяем
    explicit: bool = False


def message_link(chat_id: int, msg_id: int) -> str | None:
    # У супергрупп id вида -100XXXXXXXXXX → ссылка t.me/c/XXXXXXXXXX/<msg>
    s = str(chat_id)
    return f"https://t.me/c/{s[4:]}/{msg_id}" if s.startswith("-100") else None


def text_key(text: str) -> str:
    # Одно и то же сообщение, пересланное дважды, не даёт дублей
    return "pm:" + hashlib.sha1(text.strip().encode()).hexdigest()[:16]


class ChatProcessor:
    def __init__(self, db: DB, extractor: Extractor, trusted: frozenset[int] = frozenset()):
        self.db = db
        self.extractor = extractor
        self.trusted = trusted

    async def process(self, msg: IncomingMessage) -> list[Task] | None:
        """Новые задания из сообщения. None — LLM недоступна (сообщение не помечено, можно повторить)."""
        if self.db.is_seen(msg.key, msg.version):
            return []
        if not msg.explicit and not is_candidate(msg.text, msg.sender_id, self.trusted):
            self.db.mark_seen(msg.key, msg.version)
            return []
        if not msg.text:
            return []

        try:
            extracted = await self.extractor.extract(msg.text, msg.sent_at, msg.chat_title)
        except anthropic.APIError:
            log.exception("LLM недоступна, сообщение %s пропущено", msg.key)
            return None

        tasks = [
            Task(source=msg.source, source_id=f"{msg.key}:{i}", title=e.title, subject=e.subject, deadline=e.deadline, url=msg.url)
            for i, e in enumerate(extracted)
        ]
        new = self.db.replace_group(msg.source, f"{msg.key}:", tasks)
        self.db.mark_seen(msg.key, msg.version)
        if new:
            log.info("Из %s: %d новых заданий", msg.key, len(new))
        return new
