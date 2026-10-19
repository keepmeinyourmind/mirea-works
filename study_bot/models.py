from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Task:
    source: str  # "tg" | "portal" | "manual"
    source_id: str  # уникален внутри (user_id, source), по нему не даём дублей
    title: str
    subject: str | None = None
    deadline: datetime | None = None  # всегда с таймзоной
    url: str | None = None
    done: bool = False
    id: int | None = None
    user_id: int | None = None  # чьё задание (Telegram id)
    points: float | None = None  # максимум баллов за задание
    # Статус в СДО: none | draft | submitted | graded | quiz
    status: str | None = None
    grade: float | None = None  # полученная оценка (для теста — лучшая попытка)
    attempts_used: int | None = None
    attempts_total: int | None = None
    auto_done: bool = False  # закрыто ботом, потому что сдано в СДО
    edited: bool = False  # правил вручную — СДО больше не перезаписывает поля
    checked_at: datetime | None = None  # когда последний раз смотрели статус в СДО


@dataclass
class User:
    id: int  # Telegram id
    name: str
    digest_hour: int = 20
    digest_minute: int = 0
    cookie_enc: str | None = None  # cookie СДО, зашифрованная (см. secrets.py)
    auth_warned: bool = False  # уже сказали, что сессия СДО истекла
    last_digest: date | None = None  # когда последний раз отправили сводку
