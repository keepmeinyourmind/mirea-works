"""Сборка текста сводки. Чистые функции: время передаётся параметром, удобно тестировать."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .models import Task

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEK_DAYS = 7


def format_deadline(d: datetime, tz: ZoneInfo) -> str:
    local = d.astimezone(tz)
    s = f"{local:%d.%m} ({WD[local.weekday()]})"
    if (local.hour, local.minute) != (23, 59):
        s += f" {local:%H:%M}"
    return s


def format_line(n: int, t: Task, tz: ZoneInfo) -> str:
    title = escape(t.title)
    if t.url:
        title = f'<a href="{escape(t.url)}">{title}</a>'
    parts = [f"{n}."]
    if t.subject:
        parts.append(f"<b>{escape(t.subject)}</b> —")
    parts.append(title)
    if t.deadline:
        parts.append(f"· до {format_deadline(t.deadline, tz)}")
    return " ".join(parts)


def build_digest(tasks: list[Task], now: datetime, tz: ZoneInfo, full: bool = False) -> tuple[str, list[Task]]:
    """Возвращает текст (HTML) и задания в порядке нумерации — для кнопок «Сделано».
    full=False — вечерняя сводка: только ближайшая неделя, остальное одной строкой."""
    today = now.astimezone(tz).date()
    sections: dict[str, list[Task]] = {
        "🔥 Просрочено": [],
        "📌 Сегодня": [],
        "⏰ Завтра": [],
        "📅 На неделе": [],
        "🗓 Позже": [],
        "🗂 Без срока": [],
    }
    for t in tasks:
        if t.deadline is None:
            sections["🗂 Без срока"].append(t)
            continue
        days = (t.deadline.astimezone(tz).date() - today).days
        if t.deadline < now:
            sections["🔥 Просрочено"].append(t)
        elif days == 0:
            sections["📌 Сегодня"].append(t)
        elif days == 1:
            sections["⏰ Завтра"].append(t)
        elif days <= WEEK_DAYS:
            sections["📅 На неделе"].append(t)
        else:
            sections["🗓 Позже"].append(t)

    later = sections["🗓 Позже"]
    if not full:
        sections["🗓 Позже"] = []

    shown: list[Task] = []
    blocks: list[str] = []
    for header, items in sections.items():
        if not items:
            continue
        lines = [f"<b>{header}</b>"]
        for t in sorted(items, key=lambda t: (t.deadline is None, t.deadline or now)):
            shown.append(t)
            lines.append(format_line(len(shown), t, tz))
        blocks.append("\n".join(lines))

    if not blocks:
        text = "Открытых заданий нет 🎉" if not later else ""
    else:
        text = "\n\n".join(blocks)
    if not full and later:
        text += f"\n\n<i>Ещё {len(later)} с дальним сроком — /tasks</i>"
    return text.strip(), shown
