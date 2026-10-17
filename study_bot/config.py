"""Настройки из .env (pydantic-settings)."""

from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram userbot (my.telegram.org)
    api_id: int
    api_hash: str
    # Супергруппы, которые читаем: CHAT_IDS=[-1001234567890]
    chat_ids: list[int]
    # Авторы, чьим сообщениям доверяем больше (староста, преподаватель): TRUSTED_SENDERS=[123,456]
    trusted_senders: list[int] = []
    backfill_days: int = 14

    # Бот для уведомлений (@BotFather) и твой user id — бот отвечает только тебе
    bot_token: str
    owner_id: int

    anthropic_api_key: str
    llm_model: str = "claude-haiku-5-5"

    tz: str = "Europe/Moscow"
    digest_hour: int = 20
    digest_minute: int = 0
    # Опрос портала заодно не даёт истечь сессии Moodle — не делать реже ~раза в час
    portal_poll_minutes: int = 30
    # Сразу писать о новом задании из чата, не дожидаясь вечерней сводки
    notify_new: bool = True

    # Портал: "moodle" (СДО МИРЭА) или "none"
    portal_kind: str = "none"
    portal_url: str = "https://online-edu.mirea.ru"
    # Насколько назад смотреть просроченные задания портала
    portal_lookback_days: int = 60

    data_dir: Path = Path("data")

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tasks.sqlite3"

    @property
    def portal_cookie_path(self) -> Path:
        # Значение cookie MoodleSession; обновляется командой /cookie
        return self.data_dir / "moodle_session.txt"

    @property
    def session_path(self) -> Path:
        # Telethon сам допишет .session
        return self.data_dir / "userbot"
