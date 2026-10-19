"""Настройки из .env (pydantic-settings)."""

from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Бот (@BotFather) и id админа (тебя): приглашает других, получает задания из групп CHAT_IDS
    bot_token: str
    owner_id: int
    # Ключ шифрования cookie СДО в базе (Fernet). Сгенерировать: python -c "from study_bot.vault import new_key; print(new_key())"
    cookie_key: str = ""
    # Если Telegram недоступен напрямую (сервер в России): socks5://xray:10808 или http://127.0.0.1:10809
    telegram_proxy: str = ""

    # Группы, которые бот читает, если его туда добавят: CHAT_IDS=[-1001234567890]
    # Пусто — бот работает только с тем, что ты ему пересылаешь
    chat_ids: list[int] = []
    # Авторы, чьим сообщениям в группе доверяем больше (староста, преподаватель)
    trusted_senders: list[int] = []

    # Пусто — бесплатный разбор правилами; с ключом — точнее, через Claude
    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-5-5"
    # Свои сокращения предметов: SUBJECT_ALIASES={"ос": "Операционные системы", "бд": "Базы данных"}
    subject_aliases: dict[str, str] = {}

    tz: str = "Europe/Moscow"
    digest_hour: int = 20
    digest_minute: int = 0
    # Если в момент сводки Mac спал — прислать её после пробуждения, но не позже чем через столько часов
    digest_grace_hours: int = 6
    # Опрос портала заодно не даёт истечь сессии Moodle — не делать реже ~раза в час
    portal_poll_minutes: int = 30
    # Статус и баллы со страниц заданий: как часто перепроверять и сколько страниц за один опрос
    portal_status_hours: int = 3
    portal_check_batch: int = 15
    # Сразу писать о новом задании из группы, не дожидаясь вечерней сводки
    notify_new: bool = True

    # Портал: "moodle" (СДО МИРЭА) или "none"
    portal_kind: str = "none"
    portal_url: str = "https://online-edu.mirea.ru"
    # Насколько назад смотреть просроченные задания портала
    portal_lookback_days: int = 60

    # Mini App: публичный https-адрес (для кнопок «Открыть»), где слушать HTTP внутри
    webapp_url: str = ""
    webapp_host: str = "127.0.0.1"
    webapp_port: int = 8080
    # Только для разработки в обычном браузере: API без проверки подписи Telegram
    webapp_dev_no_auth: bool = False

    data_dir: Path = Path("data")

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tasks.sqlite3"

    @property
    def legacy_cookie_path(self) -> Path:
        # Cookie однопользовательской версии — при запуске переносится в базу (зашифрованной) и удаляется
        return self.data_dir / "moodle_session.txt"
