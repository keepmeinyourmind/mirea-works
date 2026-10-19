"""Сценарии бота целиком: настоящие обновления Telegram через диспетчер aiogram, сеть подменена."""

from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser

from study_bot.bot import CLOSED, Notifier, build_router
from study_bot.config import Settings
from study_bot.db import DB
from study_bot.extract.rules import RulesExtractor
from study_bot.models import Task
from study_bot.sources.chat import ChatProcessor
from study_bot.vault import Vault, new_key

ADMIN, FRIEND, STRANGER = 777, 888, 999
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


class RecordingSession(BaseSession):
    """Вместо Telegram: запоминает, что бот отправил, и отвечает правдоподобными объектами."""

    def __init__(self):
        super().__init__()
        self.sent: list = []

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, GetMe):
            return TgUser(id=1, is_bot=True, first_name="bot", username="mireahometaskbot")
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or 0
            return Message(message_id=1, date=NOW, chat=Chat(id=chat_id, type="private"), text=method.text)
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    def texts(self, chat_id: int | None = None) -> list[str]:
        return [m.text for m in self.sent if isinstance(m, SendMessage) and (chat_id is None or m.chat_id == chat_id)]


class Harness:
    def __init__(self):
        self.s = Settings(_env_file=None, bot_token="1:x", owner_id=ADMIN)
        self.db = DB(":memory:")
        self.db.add_user(ADMIN, "админ")
        self.vault = Vault(new_key())
        self.notifier = Notifier(self.s, self.db)
        self.session = RecordingSession()
        self.notifier.bot = Bot("1:x", session=self.session, default=self.notifier.bot.default)
        self.polled: list[int] = []
        extractor = RulesExtractor(self.s.zone, lambda: [])

        async def poll_user(user, warn=True):
            self.polled.append(user.id)
            return 3

        self.dp = Dispatcher()
        self.dp.include_router(build_router(self.notifier, self.db, extractor, ChatProcessor(self.db, extractor),
                                            self.vault, poll_user))
        self.update_id = 0

    async def send(self, user_id: int, text: str, first_name: str = "Аня"):
        self.update_id += 1
        user = TgUser(id=user_id, is_bot=False, first_name=first_name)
        msg = Message(message_id=self.update_id, date=NOW, chat=Chat(id=user_id, type="private"), from_user=user, text=text)
        await self.dp.feed_update(self.notifier.bot, Update(update_id=self.update_id, message=msg))

    async def press(self, user_id: int, data: str):
        self.update_id += 1
        user = TgUser(id=user_id, is_bot=False, first_name="x")
        msg = Message(message_id=1, date=NOW, chat=Chat(id=user_id, type="private"), text="…")
        cb = CallbackQuery(id=str(self.update_id), from_user=user, chat_instance="c", data=data, message=msg)
        await self.dp.feed_update(self.notifier.bot, Update(update_id=self.update_id, callback_query=cb))

    def last(self, chat_id: int) -> str:
        return self.session.texts(chat_id)[-1]

    def alerts(self) -> list[str]:
        return [m.text for m in self.session.sent if isinstance(m, AnswerCallbackQuery)]


@pytest.fixture
def h():
    return Harness()


async def test_stranger_gets_closed_message_everywhere(h):
    await h.send(STRANGER, "/start")
    await h.send(STRANGER, "/digest")
    await h.send(STRANGER, "до 25.10 лаба")
    await h.send(STRANGER, "/start неверныйкод")
    assert h.session.texts(STRANGER) == [CLOSED] * 4
    assert h.db.get_user(STRANGER) is None


async def test_invite_flow(h):
    await h.send(ADMIN, "/invite")
    link = h.last(ADMIN).split("\n")[-1]
    assert link.startswith("https://t.me/mireahometaskbot?start=")
    code = link.split("=", 1)[1]

    await h.send(FRIEND, f"/start {code}", first_name="Аня")
    assert h.db.get_user(FRIEND).name == "Аня"
    friend_texts = h.session.texts(FRIEND)
    assert any("Как подключить СДО" in t for t in friend_texts) and any("Что я храню" in t for t in friend_texts)
    assert "подключился по приглашению" in h.last(ADMIN)

    # ссылка одноразовая
    await h.send(STRANGER, f"/start {code}")
    assert h.last(STRANGER) == CLOSED and h.db.get_user(STRANGER) is None


async def test_friend_settings_cookie_and_forget(h):
    h.db.add_user(FRIEND, "Аня")
    await h.send(FRIEND, "/time 21:30")
    assert (h.db.get_user(FRIEND).digest_hour, h.db.get_user(FRIEND).digest_minute) == (21, 30)
    await h.send(FRIEND, "/time завтра")
    assert "Сейчас сводка приходит в 21:30" in h.last(FRIEND)

    await h.send(FRIEND, "/cookie MoodleSession=abc123")
    assert any(isinstance(m, DeleteMessage) for m in h.session.sent)  # сообщение с секретом удалено
    enc = h.db.get_user(FRIEND).cookie_enc
    assert enc and "abc123" not in enc and h.vault.decrypt(enc) == "abc123"  # в базе — только шифр
    assert h.polled == [FRIEND] and "СДО подключено" in h.last(FRIEND)

    await h.send(FRIEND, "/forget")
    await h.press(FRIEND, "forget:yes")
    assert h.db.get_user(FRIEND) is None


async def test_admin_commands_are_admin_only(h):
    h.db.add_user(FRIEND, "Аня")
    await h.send(FRIEND, "/invite")
    await h.send(FRIEND, f"/kick {ADMIN}")
    assert h.session.texts(FRIEND) == ["Не знаю такой команды. /help"] * 2
    assert h.db.get_user(ADMIN)

    await h.send(ADMIN, "/users")
    assert "Аня" in h.last(ADMIN) and str(FRIEND) in h.last(ADMIN)
    await h.send(ADMIN, f"/kick {FRIEND}")
    assert h.db.get_user(FRIEND) is None


async def test_cannot_mark_someone_elses_task(h):
    h.db.add_user(FRIEND, "Аня")
    h.db.upsert(Task(source="manual", source_id="1", title="моё", user_id=ADMIN))
    tid = h.db.list_open(ADMIN)[0].id
    await h.press(FRIEND, f"done:{tid}")
    assert h.alerts()[-1] == "Уже отмечено" and not h.db.get(ADMIN, tid).done
    await h.press(ADMIN, f"done:{tid}")
    assert h.db.get(ADMIN, tid).done


async def test_forwarded_task_goes_to_sender(h):
    h.db.add_user(FRIEND, "Аня")
    await h.send(FRIEND, "До 25.10 сдать лабу 3")
    assert [t.title for t in h.db.list_open(FRIEND)] == ["До 25.10 сдать лабу 3"]
    assert h.db.list_open(ADMIN) == []


async def test_status_is_admin_only(h):
    h.db.add_user(FRIEND, "Аня")
    await h.send(FRIEND, "/status")
    assert h.last(FRIEND) == "Не знаю такой команды. /help"
    await h.send(ADMIN, "/status")
    assert "Состояние" in h.last(ADMIN) and "Пользователи</b>: 2" in h.last(ADMIN)
