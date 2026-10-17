"""Конвейер сообщение → фильтр → LLM → БД без сети: LLM и сообщения Telegram подменены."""

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from study_bot.config import Settings
from study_bot.db import DB
from study_bot.extract.llm import ExtractedTask
from study_bot.sources.telegram import TelegramReader, message_link

MSK = ZoneInfo("Europe/Moscow")
CHAT = -1001234567890


@dataclass
class FakeMsg:
    id: int
    message: str
    date: datetime
    chat_id: int = CHAT
    sender_id: int = 42
    edit_date: datetime | None = None


class FakeExtractor:
    def __init__(self, result: list[ExtractedTask]):
        self.result = result
        self.calls = 0

    async def extract(self, text, sent_at, chat_title=None):
        self.calls += 1
        return self.result


@pytest.fixture
def setup(tmp_path):
    s = Settings(
        _env_file=None, api_id=1, api_hash="x", chat_ids=[CHAT], bot_token="1:x",
        owner_id=1, anthropic_api_key="x", data_dir=tmp_path,
    )
    db = DB(":memory:")
    notified = []

    async def on_new(tasks):
        notified.extend(tasks)

    def make(result):
        ex = FakeExtractor(result)
        return TelegramReader(s, db, ex, on_new=on_new), ex

    return db, notified, make


SENT = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
LAB = ExtractedTask("Лаба 2", "ОС", datetime(2026, 10, 25, 23, 59, tzinfo=MSK))


async def test_task_message_is_saved_and_notified(setup):
    db, notified, make = setup
    reader, ex = make([LAB])
    await reader.process(FakeMsg(10, "До 25.10 сдать лабу 2 по ОС", SENT), notify=True)
    [t] = db.list_open()
    assert (t.title, t.subject, t.url) == ("Лаба 2", "ОС", "https://t.me/c/1234567890/10")
    assert [n.title for n in notified] == ["Лаба 2"]


async def test_same_message_not_sent_to_llm_twice(setup):
    db, _, make = setup
    reader, ex = make([LAB])
    msg = FakeMsg(10, "До 25.10 сдать лабу 2 по ОС", SENT)
    await reader.process(msg, notify=False)
    await reader.process(msg, notify=False)
    assert ex.calls == 1
    assert len(db.list_open()) == 1


async def test_chatter_skips_llm(setup):
    db, _, make = setup
    reader, ex = make([LAB])
    await reader.process(FakeMsg(11, "Кто идёт в столовку?", SENT), notify=True)
    assert ex.calls == 0
    assert db.list_open() == []


async def test_edited_message_is_reprocessed(setup):
    db, notified, make = setup
    reader, ex = make([LAB])
    await reader.process(FakeMsg(10, "До 25.10 сдать лабу 2 по ОС", SENT), notify=True)
    ex.result = []  # после правки задания в сообщении больше нет
    edited = FakeMsg(10, "Лабу 2 отменили, сдавать не надо", SENT, edit_date=datetime(2026, 10, 17, 11, 0, tzinfo=timezone.utc))
    await reader.process(edited, notify=True)
    assert ex.calls == 2
    assert db.list_open() == []


def test_message_link_only_for_supergroups():
    assert message_link(-1001234567890, 5) == "https://t.me/c/1234567890/5"
    assert message_link(12345, 5) is None
