"""Бот-уведомлялка (aiogram 3). Отвечает только владельцу."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import anthropic
from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import Settings
from .db import DB
from .digest import build_digest, format_line
from .extract.llm import Extractor
from .models import Task
from .sources.moodle import save_cookie

log = logging.getLogger(__name__)

MAX_BUTTONS = 50
HELP = (
    "Я собираю задания из учебного чата и портала и каждый вечер присылаю сводку.\n\n"
    "/digest — сводка сейчас\n"
    "/tasks — все открытые задания\n"
    "/add <текст> — добавить задание вручную, например: /add до 30.10 реферат по истории\n"
    "/cookie <значение> — обновить сессию портала"
)
COOKIE_HELP = (
    "⚠️ Нет доступа к СДО — задания с портала не обновляются.\n\n"
    "1. Войди на online-edu.mirea.ru в браузере.\n"
    "2. DevTools (F12 / ⌥⌘I) → Application → Cookies → online-edu.mirea.ru → скопируй значение <code>MoodleSession</code>.\n"
    "3. Пришли мне: <code>/cookie значение</code> (сообщение я сразу удалю)."
)


def done_keyboard(tasks: list[Task]) -> InlineKeyboardMarkup | None:
    if not tasks:
        return None
    kb = InlineKeyboardBuilder()
    for n, t in enumerate(tasks[:MAX_BUTTONS], start=1):
        kb.button(text=f"✅ {n}", callback_data=f"done:{t.id}")
    kb.adjust(5)
    return kb.as_markup()


class Notifier:
    def __init__(self, settings: Settings, db: DB):
        self.s = settings
        self.db = db
        self.bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))

    def now(self) -> datetime:
        return datetime.now(self.s.zone)

    async def send_digest(self, full: bool = False) -> None:
        text, shown = build_digest(self.db.list_open(), self.now(), self.s.zone, full=full)
        await self.bot.send_message(self.s.owner_id, text, reply_markup=done_keyboard(shown))

    async def notify_new(self, tasks: list[Task]) -> None:
        lines = [format_line(n, t, self.s.zone) for n, t in enumerate(tasks, start=1)]
        title = "🆕 Новое задание" if len(tasks) == 1 else "🆕 Новые задания"
        await self.bot.send_message(self.s.owner_id, f"<b>{title}</b>\n" + "\n".join(lines), reply_markup=done_keyboard(tasks))

    async def say(self, text: str) -> None:
        await self.bot.send_message(self.s.owner_id, text)


def build_router(
    notifier: Notifier, db: DB, extractor: Extractor, poll_portal: Callable[..., Awaitable[int | None]]
) -> Router:
    router = Router()
    owner = F.from_user.id == notifier.s.owner_id
    router.message.filter(owner)
    router.callback_query.filter(owner)

    @router.message(Command("start", "help"))
    async def help_(m: Message) -> None:
        await m.answer(HELP)

    @router.message(Command("digest"))
    async def digest(m: Message) -> None:
        await notifier.send_digest()

    @router.message(Command("tasks"))
    async def tasks(m: Message) -> None:
        await notifier.send_digest(full=True)

    @router.message(Command("add"))
    async def add(m: Message, command: CommandObject) -> None:
        text = (command.args or "").strip()
        if not text:
            await m.answer("Напиши задание после команды: /add до 30.10 реферат по истории")
            return
        try:
            extracted = await extractor.extract(text, m.date)
        except anthropic.APIError:
            log.exception("LLM недоступна в /add")
            extracted = []
        if not extracted:
            # LLM не нашла задание или упала — сохраняем как есть, без срока
            extracted_tasks = [Task(source="manual", source_id=f"{m.message_id}:0", title=text)]
        else:
            extracted_tasks = [
                Task(source="manual", source_id=f"{m.message_id}:{i}", title=e.title, subject=e.subject, deadline=e.deadline)
                for i, e in enumerate(extracted)
            ]
        new = db.replace_group("manual", f"{m.message_id}:", extracted_tasks)
        if new:
            await notifier.notify_new(new)

    @router.message(Command("cookie"))
    async def cookie(m: Message, command: CommandObject) -> None:
        value = (command.args or "").strip()
        # В сообщении секрет — убираем его из истории чата сразу
        try:
            await m.delete()
        except Exception:
            log.warning("Не смог удалить сообщение с cookie — удали его вручную")
        if not value:
            await m.answer(COOKIE_HELP)
            return
        save_cookie(notifier.s.portal_cookie_path, value.removeprefix("MoodleSession="))
        count = await poll_portal(warn=False)
        if count is None:
            await m.answer("❌ С этой cookie портал не пускает. Проверь, что скопировал MoodleSession после входа.")
        else:
            await m.answer(f"✅ Портал подключён, заданий с дедлайнами: {count}. Сводка — /digest")

    @router.callback_query(F.data.startswith("done:"))
    async def done(cb: CallbackQuery) -> None:
        task_id = int(cb.data.split(":", 1)[1])
        task = db.get(task_id)
        if task and db.mark_done(task_id):
            await cb.answer(f"Сделано: {task.title[:60]}")
        else:
            await cb.answer("Уже отмечено")

    return router
