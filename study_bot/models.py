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
    points: float | None = None  # максимум баллов за задание
    # Статус в СДО: none | draft | submitted | graded | quiz
    status: str | None = None
    grade: float | None = None  # полученная оценка (для теста — лучшая попытка)
    attempts_used: int | None = None
    attempts_total: int | None = None
    auto_done: bool = False  # закрыто ботом, потому что сдано в СДО
    edited: bool = False  # правил вручную — СДО больше не перезаписывает поля
    checked_at: datetime | None = None  # когда последний раз смотрели статус в СДО
