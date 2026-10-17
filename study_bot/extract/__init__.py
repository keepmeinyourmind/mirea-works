from datetime import datetime
from typing import Protocol

from .llm import ExtractedTask


class TaskExtractor(Protocol):
    """Разбор текста в задания: RulesExtractor (бесплатно) или llm.Extractor (Claude)."""

    async def extract(self, text: str, sent_at: datetime, chat_title: str | None = None) -> list[ExtractedTask]: ...
