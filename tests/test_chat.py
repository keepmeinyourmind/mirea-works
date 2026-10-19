"""Сообщение → фильтр → LLM → БД без сети: LLM подменена, сообщения — настоящие объекты aiogram."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.types import Chat, Message, MessageOriginUser, User

from study_bot.bot import from_message
from study_bot.db import DB
from study_bot.extract.llm import ExtractedTask
from study_bot.sources.chat import ChatProcessor, message_link

MSK = ZoneInfo("Europe/Moscow")
GROUP = Chat(id=-1001234567890, type="supergroup", title="ИКБО-01-24")
ME_CHAT = Chat(id=777, type="private")
ANYA = User(id=42, is_bot=False, first_name="Аня")
SENT = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
LAB = ExtractedTask("Лаба 2", "ОС", datetime(2026, 10, 25, 23, 59, tzinfo=MSK))


class FakeExtractor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def extract(self, text, sent_at, chat_title=None):
        self.calls.append((text, sent_at, chat_title))
        return self.result


def group_msg(text: str, msg_id: int = 10, edit_date=None) -> Message:
    return Message(message_id=msg_id, date=SENT, chat=GROUP, from_user=ANYA, text=text, edit_date=edit_date)


ME, FRIEND = 777, 888


def from_message_g(m):
    return from_message(m, ME, explicit=False)


def setup(result):
    db = DB(":memory:")
    ex = FakeExtractor(result)
    return db, ex, ChatProcessor(db, ex)


async def test_group_task_is_saved_with_link():
    db, ex, proc = setup([LAB])
    new = await proc.process(from_message_g(group_msg("До 25.10 сдать лабу 2 по ОС")))
    assert [t.title for t in new] == ["Лаба 2"]
    [t] = db.list_open(ME)
    assert (t.source, t.url) == ("tg", "https://t.me/c/1234567890/10")
    assert ex.calls[0][2] == "ИКБО-01-24"


async def test_group_chatter_skips_llm():
    db, ex, proc = setup([LAB])
    assert await proc.process(from_message_g(group_msg("Кто идёт в столовку?"))) == []
    assert ex.calls == []


async def test_same_group_message_not_sent_to_llm_twice():
    db, ex, proc = setup([LAB])
    msg = from_message_g(group_msg("До 25.10 сдать лабу 2 по ОС"))
    await proc.process(msg)
    await proc.process(msg)
    assert len(ex.calls) == 1


async def test_edited_group_message_replaces_tasks():
    db, ex, proc = setup([LAB])
    await proc.process(from_message_g(group_msg("До 25.10 сдать лабу 2 по ОС")))
    ex.result = []
    # В Bot API edit_date — unix-время
    edited = group_msg("Лабу 2 отменили, сдавать не надо", edit_date=int(SENT.timestamp()) + 3600)
    await proc.process(from_message_g(edited))
    assert len(ex.calls) == 2
    assert db.list_open(ME) == []


async def test_forward_uses_original_date_and_skips_prefilter():
    db, ex, proc = setup([LAB])
    original = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)  # переслали через 5 дней
    fwd = Message(
        message_id=500, date=SENT, chat=ME_CHAT, from_user=ANYA, text="Ребята, к пятнице",  # нет ключевых слов задачи
        forward_origin=MessageOriginUser(type="user", date=original, sender_user=ANYA),
    )
    new = await proc.process(from_message(fwd, ME, explicit=True))
    assert [t.source for t in new] == ["manual"]
    assert ex.calls[0][1] == original  # «к пятнице» считается от даты оригинала


async def test_same_text_forwarded_twice_is_not_duplicated():
    db, ex, proc = setup([LAB])
    a = Message(message_id=1, date=SENT, chat=ME_CHAT, from_user=ANYA, text="ДЗ: лаба 2")
    b = Message(message_id=2, date=SENT, chat=ME_CHAT, from_user=ANYA, text="ДЗ: лаба 2 ")
    await proc.process(from_message(a, ME, explicit=True))
    assert await proc.process(from_message(b, ME, explicit=True)) == []
    assert len(db.list_open(ME)) == 1


def test_message_link_only_for_supergroups():
    assert message_link(-1001234567890, 5) == "https://t.me/c/1234567890/5"
    assert message_link(12345, 5) is None


async def test_rules_extractor_end_to_end_uses_portal_subjects():
    from study_bot.extract.rules import RulesExtractor
    from study_bot.models import Task

    db = DB(":memory:")
    # Курс уже пришёл с портала — его название становится словарём предметов
    db.upsert(Task(source="portal", source_id="1", title="КР 1", subject="Фронтенд и бэкенд разработка", user_id=ME))
    proc = ChatProcessor(db, RulesExtractor(MSK, lambda: db.subjects()))
    fwd = Message(message_id=9, date=SENT, chat=ME_CHAT, from_user=ANYA, text="К пятнице сдать лабу 4 по фронтенду")
    [t] = await proc.process(from_message(fwd, ME, explicit=True))
    assert t.subject == "Фронтенд и бэкенд разработка"
    assert t.deadline.date().isoformat() == "2026-10-23"  # 17.10 — суббота → пятница 23.10


async def test_same_text_from_two_users_goes_to_each():
    db, ex, proc = setup([LAB])
    a = Message(message_id=1, date=SENT, chat=ME_CHAT, from_user=ANYA, text="ДЗ: лаба 2")
    await proc.process(from_message(a, ME, explicit=True))
    assert await proc.process(from_message(a, FRIEND, explicit=True))  # у друга — своё задание
    assert len(db.list_open(ME)) == 1 and len(db.list_open(FRIEND)) == 1
