from dataclasses import dataclass
from datetime import datetime


@dataclass
class Task:
    source: str  # "tg" | "portal" | "manual"
    source_id: str  # уникален внутри source, по нему не даём дублей
    title: str
    subject: str | None = None
    deadline: datetime | None = None  # всегда с таймзоной
    url: str | None = None
    done: bool = False
    id: int | None = None
