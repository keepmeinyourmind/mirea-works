"""Дешёвый фильтр перед LLM: отсекаем болтовню, чтобы не платить за каждое «го в столовку»."""

import re

KEYWORDS = re.compile(
    r"""
    \bд/?з\b | домашк | задани | \bсда(ть|ём|ем|ча|чи) | дедлайн | \bсрок |
    \bлаб | \bпракт | \bкр\b | контрольн | курсов | реферат | доклад | эссе |
    презентац | \bтест | коллоквиум | экзамен | зач[её]т | \bвариант |
    \bдо\s+\d{1,2}[./]\d{1,2} | \bк\s+(понедельник|вторник|сред|четверг|пятниц|суббот|воскресень|следующ|завтр)
    """,
    re.IGNORECASE | re.VERBOSE,
)

MIN_LEN = 12


def is_candidate(text: str | None, sender_id: int | None = None, trusted: frozenset[int] = frozenset()) -> bool:
    if not text or len(text.strip()) < MIN_LEN:
        return False
    if KEYWORDS.search(text):
        return True
    # Староста/преподаватель пишет длинное объявление без ключевых слов — пусть решает LLM
    return sender_id in trusted and len(text) >= 40
