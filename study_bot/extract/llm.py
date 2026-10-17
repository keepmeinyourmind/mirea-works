"""Извлечение заданий из сообщения через Claude со structured output (JSON по схеме)."""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import anthropic

log = logging.getLogger(__name__)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": NULLABLE_STR,
                    "title": {"type": "string"},
                    "deadline_date": NULLABLE_STR,  # YYYY-MM-DD
                    "deadline_time": NULLABLE_STR,  # HH:MM
                },
                "required": ["subject", "title", "deadline_date", "deadline_time"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tasks"],
    "additionalProperties": False,
}

SYSTEM = """Ты разбираешь сообщения из учебного чата студенческой группы и достаёшь из них учебные задания: \
домашние работы, лабораторные, контрольные, тесты, рефераты, курсовые, экзамены и зачёты — всё, к чему \
студенту нужно подготовиться или что сдать к сроку.

Правила:
- Если в сообщении нет задания (болтовня, вопрос, перенос аудитории без задания), верни пустой список.
- title — коротко и по делу, на русском: что сделать («Лаба 2: сдать отчёт», «Номера 120–135»).
- subject — предмет, если понятен из текста, иначе null. Не выдумывай.
- deadline_date — дата в формате YYYY-MM-DD. Относительные сроки («к пятнице», «на следующей неделе», \
«завтра») считай от даты сообщения. «К пятнице» — ближайшая пятница после даты сообщения. \
Если срока нет — null.
- deadline_time — HH:MM, только если время указано явно, иначе null.
- Одно сообщение может содержать несколько заданий — верни каждое отдельно."""


@dataclass
class ExtractedTask:
    title: str
    subject: str | None
    deadline: datetime | None


def build_prompt(text: str, sent_at: datetime, chat_title: str | None, tz: ZoneInfo) -> str:
    local = sent_at.astimezone(tz)
    header = f"Дата сообщения: {local:%Y-%m-%d} ({WEEKDAYS[local.weekday()]}), {local:%H:%M}"
    if chat_title:
        header += f"\nЧат: {chat_title}"
    return f"{header}\n\nСообщение:\n{text}"


def parse_tasks(data: dict, tz: ZoneInfo) -> list[ExtractedTask]:
    """Превращает JSON от модели в задания. Битые даты не роняют разбор — просто срок = None."""
    result = []
    for item in data.get("tasks", []):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        deadline = None
        if d := item.get("deadline_date"):
            try:
                day = date.fromisoformat(d)
                t = time(23, 59)
                if tm := item.get("deadline_time"):
                    try:
                        t = time.fromisoformat(tm)
                    except ValueError:
                        pass
                deadline = datetime.combine(day, t, tzinfo=tz)
            except ValueError:
                log.warning("LLM вернула кривую дату: %r", d)
        subject = (item.get("subject") or "").strip() or None
        result.append(ExtractedTask(title=title, subject=subject, deadline=deadline))
    return result


class Extractor:
    def __init__(self, api_key: str, model: str, tz: ZoneInfo):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.tz = tz

    async def extract(self, text: str, sent_at: datetime, chat_title: str | None = None) -> list[ExtractedTask]:
        """Бросает anthropic.APIError при сетевых/серверных ошибках — вызывающий решает, повторять ли."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(text, sent_at, chat_title, self.tz)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if response.stop_reason != "end_turn":
            log.warning("LLM не довела ответ до конца: stop_reason=%s", response.stop_reason)
            return []
        raw = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return parse_tasks(json.loads(raw), self.tz)
        except json.JSONDecodeError:
            log.warning("LLM вернула не JSON: %r", raw[:200])
            return []
