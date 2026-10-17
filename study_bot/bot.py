"""Бот (aiogram 3). Личка — только владелец; группы — только из CHAT_IDS."""

import html
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import anthropic
from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import Settings
from .db import DB
from .digest import build_digest, format_line
from .extract import TaskExtractor
from .models import Task
from .sources.chat import ChatProcessor, IncomingMessage, message_link, text_key
from .sources.moodle import save_cookie

log = logging.getLogger(__name__)

MAX_BUTTONS = 50
HELP = html.escape(
    "Я собираю задания из учебного чата и портала и каждый вечер присылаю сводку.\n\n"
    "Перешли мне сообщение из группы (или просто напиши текст) — достану из него задания.\n\n"
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


def app_url(base: str, task_id: int | None = None) -> str | None:
    """Адрес Mini App (только https — иначе Telegram не примет кнопку). task — сразу в карточку."""
    if not base.startswith("https://"):
        return None
    base = base.rstrip("/") + "/"
    return f"{base}?task={task_id}" if task_id else base


def done_keyboard(tasks: list[Task], webapp: str = "", open_task: int | None = None) -> InlineKeyboardMarkup | None:
    kb = InlineKeyboardBuilder()
    for n, t in enumerate(tasks[:MAX_BUTTONS], start=1):
        kb.button(text=f"✅ {n}", callback_data=f"done:{t.id}")
    n = min(len(tasks), MAX_BUTTONS)
    sizes = [5] * (n // 5) + ([n % 5] if n % 5 else [])  # ряды по 5 галочек, «Открыть» — отдельным рядом
    if url := app_url(webapp, open_task):
        kb.button(text="✏️ Открыть" if open_task else "📋 Открыть приложение", web_app=WebAppInfo(url=url))
        sizes.append(1)
    if not sizes:
        return None
    kb.adjust(*sizes)
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
        await self.bot.send_message(self.s.owner_id, text, reply_markup=done_keyboard(shown, self.s.webapp_url))

    async def notify_new(self, tasks: list[Task]) -> None:
        lines = [format_line(n, t, self.s.zone) for n, t in enumerate(tasks, start=1)]
        title = "🆕 Новое задание" if len(tasks) == 1 else "🆕 Новые задания"
        one = tasks[0].id if len(tasks) == 1 else None
        await self.bot.send_message(
            self.s.owner_id, f"<b>{title}</b>\n" + "\n".join(lines),
            reply_markup=done_keyboard(tasks, self.s.webapp_url, open_task=one),
        )

    async def say(self, text: str) -> None:
        await self.bot.send_message(self.s.owner_id, text)

    async def setup_menu_button(self) -> None:
        """Кнопка «Задания» слева от поля ввода в чате с ботом — открывает Mini App."""
        if url := app_url(self.s.webapp_url):
            await self.bot.set_chat_menu_button(
                chat_id=self.s.owner_id, menu_button=MenuButtonWebApp(text="Задания", web_app=WebAppInfo(url=url))
            )


def from_message(m: Message, explicit: bool) -> IncomingMessage:
    text = m.text or m.caption
    if explicit:
        # Пересланное/написанное в личку: дата оригинала, если это пересылка
        origin = m.forward_origin
        return IncomingMessage(
            key=text_key(text or ""),
            version="1",
            text=text,
            sent_at=origin.date if origin else m.date,
            source="manual",
            explicit=True,
        )
    return IncomingMessage(
        key=f"{m.chat.id}:{m.message_id}",
        version=(m.edit_date and str(m.edit_date)) or "0",
        text=text,
        sent_at=m.date,
        sender_id=m.from_user.id if m.from_user else None,
        chat_title=m.chat.title,
        url=message_link(m.chat.id, m.message_id),
    )


def build_group_router(notifier: Notifier, processor: ChatProcessor) -> Router:
    """Если бота добавили в учебную группу: читаем её сам, без твоего аккаунта."""
    router = Router()
    groups = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
    allowed = F.chat.id.in_(set(notifier.s.chat_ids))

    @router.my_chat_member(groups)
    async def added(ev: ChatMemberUpdated) -> None:
        if ev.new_chat_member.status in ("member", "administrator") and ev.chat.id not in notifier.s.chat_ids:
            # Чужая группа не тратит LLM, пока ты сам её не разрешишь
            await notifier.say(
                f"Меня добавили в группу «{html.escape(ev.chat.title or '')}». Чтобы читать её, допиши в .env: "
                f"<code>CHAT_IDS=[{ev.chat.id}]</code> и перезапусти бота."
            )

    @router.message(groups, allowed)
    @router.edited_message(groups, allowed)
    async def group_message(m: Message) -> None:
        new = await processor.process(from_message(m, explicit=False))
        if new and notifier.s.notify_new:
            await notifier.notify_new(new)

    return router


def build_router(
    notifier: Notifier, db: DB, extractor: TaskExtractor, processor: ChatProcessor,
    poll_portal: Callable[..., Awaitable[int | None]],
) -> Router:
    router = Router()
    owner = F.from_user.id == notifier.s.owner_id
    router.message.filter(owner, F.chat.type == ChatType.PRIVATE)
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

    @router.message(F.text | F.caption)
    async def forwarded_or_text(m: Message) -> None:
        # Всё, что не команда: пересланное из группы или написанное тобой
        if (m.text or "").startswith("/"):
            await m.answer("Не знаю такой команды. /help")
            return
        msg = from_message(m, explicit=True)
        if db.is_seen(msg.key, msg.version):
            await m.answer("Это сообщение я уже разбирал.")
            return
        new = await processor.process(msg)
        if new is None:
            await m.answer("Разбор сейчас недоступен — пришли ещё раз позже или добавь через /add.")
        elif new:
            await notifier.notify_new(new)
        else:
            await m.answer("Не нашёл здесь задания. Если оно есть — /add текст задания.")

    @router.callback_query(F.data.startswith("done:"))
    async def done(cb: CallbackQuery) -> None:
        task_id = int(cb.data.split(":", 1)[1])
        task = db.get(task_id)
        if task and db.mark_done(task_id):
            await cb.answer(f"Сделано: {task.title[:60]}")
        else:
            await cb.answer("Уже отмечено")

    return router
