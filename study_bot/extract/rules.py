"""Бесплатный разбор заданий правилами — без LLM и без интернета.

Понимает сроки: «до 25.10», «25.10.2026», «25 октября», «к пятнице», «в пт», «сегодня», «завтра»,
«послезавтра», «через 3 дня», «через неделю» и время «в 10:30». Предмет ищет по названиям
курсов с портала и по сокращениям («матан», «ОС»). Одно сообщение со списком по строкам даёт
несколько заданий.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .llm import ExtractedTask
from .prefilter import KEYWORDS

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
# Порядок: длинные формы раньше коротких («пт» после «пятниц»)
WEEKDAYS = {
    "понедельник": 0, "пн": 0, "вторник": 1, "вт": 1, "сред": 2, "ср": 2, "четверг": 3, "чт": 3,
    "пятниц": 4, "пт": 4, "суббот": 5, "сб": 5, "воскресень": 6, "вс": 6,
}

NUM_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
WORD_DATE = re.compile(r"\b(\d{1,2})\s+(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*", re.I)
WEEKDAY = re.compile(
    r"\b(?:к|до|в|во|на|по)\s+(?:эт[оуиа][йм]?\s+|следующ[а-я]*\s+)?"
    r"(понедельник|вторник|сред|четверг|пятниц|суббот|воскресень|пн|вт|ср|чт|пт|сб|вс)[а-я]*\b",
    re.I,
)
RELATIVE = re.compile(r"\b(сегодня|послезавтра|завтра)\b", re.I)
IN_DAYS = re.compile(r"\bчерез\s+(\d+|неделю|две недели)\s*(дн[а-я]*|день|недел[а-я]*)?", re.I)
TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
MOVED = re.compile(r"перенес|перенос|сдвинул|передвинул", re.I)
# «лаба 2.1», «№ 3.5», «упр. 1.2» — нумерация, а не дата
NUMBERING_CTX = re.compile(r"(№|(номер|лаб|упр|задач|задани|пункт|стр|вариант)[а-я.]*|§|п\.)\s*$", re.I)

# Обращения и вводные, которые не нужны в названии задания
LEAD = re.compile(r"^\s*(?:ребят[а-я]*|всем привет|привет[а-я]*|напоминаю|внимание|итак|так)[\s,!:.—-]*", re.I)
LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-•*–—])\s*")

DEFAULT_ALIASES = {
    "матан": "Математический анализ",
    "линал": "Линейная алгебра",
    "линейк": "Линейная алгебра",
    "дискретк": "Дискретная математика",
    "физр": "Физическая культура",
    "физкультур": "Физическая культура",
    "англ": "Иностранный язык",
    "инглиш": "Иностранный язык",
}
# Слова из названий курсов, которые сами по себе не говорят о предмете
COMMON = {"основы", "введение", "разработка", "технологии", "системы", "методы", "теория", "практикум",
          "информационные", "программирования", "часть", "задач", "индустриальных", "индустриального"}

MAX_TITLE = 100


def _future(d: date, sent: date) -> date:
    # «25.01» в октябре — это январь следующего года
    return d.replace(year=d.year + 1) if d < sent - timedelta(days=30) else d


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


@dataclass
class _Found:
    pos: int
    day: date


def find_dates(text: str, sent: date) -> list[_Found]:
    """Все сроки в тексте в порядке появления."""
    found: list[_Found] = []
    for m in NUM_DATE.finditer(text):
        if NUMBERING_CTX.search(text[max(0, m.start() - 12) : m.start()]):
            continue
        d, mo, y = int(m[1]), int(m[2]), m[3]
        if y:
            year = int(y) + (2000 if len(y) == 2 else 0)
            day = _safe_date(year, mo, d)
        else:
            day = _safe_date(sent.year, mo, d)
            day = day and _future(day, sent)
        if day:
            found.append(_Found(m.start(), day))
    for m in WORD_DATE.finditer(text):
        month = next(v for k, v in MONTHS.items() if m[2].lower().startswith(k))
        day = _safe_date(sent.year, month, int(m[1]))
        if day:
            found.append(_Found(m.start(), _future(day, sent)))
    for m in WEEKDAY.finditer(text):
        wd = next(v for k, v in WEEKDAYS.items() if m[1].lower().startswith(k))
        ahead = (wd - sent.weekday()) % 7 or 7  # «к пятнице», сказанное в пятницу, — следующая
        if "следующ" in m[0].lower() and ahead < 7 - sent.weekday():
            ahead += 7  # «к следующей среде» в понедельник — среда через неделю
        found.append(_Found(m.start(), sent + timedelta(days=ahead)))
    for m in RELATIVE.finditer(text):
        shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[m[1].lower()]
        found.append(_Found(m.start(), sent + timedelta(days=shift)))
    for m in IN_DAYS.finditer(text):
        n = m[1].lower()
        unit = (m[2] or "").lower()
        if n == "неделю":
            days = 7
        elif n == "две недели":
            days = 14
        else:
            days = int(n) * (7 if unit.startswith("недел") else 1)
        found.append(_Found(m.start(), sent + timedelta(days=days)))
    return sorted(found, key=lambda f: f.pos)


def pick_deadline(text: str, sent_at: datetime, tz: ZoneInfo) -> datetime | None:
    local = sent_at.astimezone(tz)
    dates = find_dates(text, local.date())
    if not dates:
        return None
    # «Перенесли с пятницы на вторник» — актуален последний срок, иначе первый
    day = (dates[-1] if MOVED.search(text) else dates[0]).day
    t = time(23, 59)
    if m := TIME.search(text):
        t = time(int(m[1]), int(m[2]))
    return datetime.combine(day, t, tzinfo=tz)


def _stems(name: str) -> list[str]:
    words = re.findall(r"[а-яёa-z]+", name.lower())
    return [w[:5] for w in words if len(w) >= 5 and w not in COMMON]


class SubjectMatcher:
    def __init__(self, subjects: Iterable[str], aliases: dict[str, str] | None = None):
        self.aliases = {k.lower(): v for k, v in {**DEFAULT_ALIASES, **(aliases or {})}.items()}
        self.subjects = {s: _stems(s) for s in subjects if s}

    def match(self, text: str) -> str | None:
        low = text.lower()
        words = re.findall(r"[а-яёa-z]+", low)
        for alias, subject in self.aliases.items():
            # Короткие сокращения (ОС, БД) — только целым словом, длинные — по началу слова
            if (len(alias) <= 3 and alias in words) or (len(alias) > 3 and any(w.startswith(alias) for w in words)):
                return subject
        stems = {w[:5] for w in words if len(w) >= 5}
        best, score = None, 0
        for subject, subject_stems in self.subjects.items():
            s = sum(1 for st in subject_stems if st in stems)
            if s > score:
                best, score = subject, s
        return best


def make_title(chunk: str) -> str:
    title = LIST_ITEM.sub("", chunk)
    title = LEAD.sub("", title).strip(" ,.:;—-")
    title = re.sub(r"\s+", " ", title)
    if title:
        title = title[0].upper() + title[1:]
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1].rstrip() + "…"


def _split(text: str) -> tuple[str, list[str]]:
    """(заголовок, пункты). Список «1. … 2. …» из двух и более пунктов — отдельные задания."""
    lines = [l for l in text.splitlines() if l.strip()]
    items = [l for l in lines if LIST_ITEM.match(l)]
    if len(items) >= 2:
        return "\n".join(l for l in lines if l not in items), items
    return text, []


def parse_rules(text: str, sent_at: datetime, tz: ZoneInfo, matcher: SubjectMatcher) -> list[ExtractedTask]:
    if not text or not text.strip():
        return []
    if not KEYWORDS.search(text) and not find_dates(text, sent_at.astimezone(tz).date()):
        return []
    header, items = _split(text)
    header_deadline = pick_deadline(header, sent_at, tz)
    header_subject = matcher.match(header)

    if not items:
        lines = [l for l in text.splitlines() if l.strip()]
        # Название — первая строка с ключевым словом, срок и предмет — из всего сообщения
        title = make_title(next((l for l in lines if KEYWORDS.search(l)), lines[0]))
        return [ExtractedTask(title=title, subject=header_subject, deadline=header_deadline)] if title else []

    tasks = []
    for item in items:
        if title := make_title(item):
            tasks.append(ExtractedTask(
                title=title,
                subject=matcher.match(item) or header_subject,
                # «До пятницы:» в заголовке — общий срок для пунктов без своего
                deadline=pick_deadline(item, sent_at, tz) or header_deadline,
            ))
    return tasks


class RulesExtractor:
    """Тот же интерфейс, что у LLM-Extractor, — подставляется в ChatProcessor и /add."""

    def __init__(self, tz: ZoneInfo, subjects: Callable[[], Iterable[str]], aliases: dict[str, str] | None = None):
        self.tz = tz
        self.subjects = subjects  # берём свежие названия курсов с портала при каждом разборе
        self.aliases = aliases

    async def extract(self, text: str, sent_at: datetime, chat_title: str | None = None) -> list[ExtractedTask]:
        return parse_rules(text, sent_at, self.tz, SubjectMatcher(self.subjects(), self.aliases))
