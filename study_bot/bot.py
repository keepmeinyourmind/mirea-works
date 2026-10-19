"""Бот (aiogram 3). Личка — только приглашённые пользователи; группы — только из CHAT_IDS (задания идут админу)."""

import html
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime

import anthropic
from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import Settings
from .db import DB
from .digest import build_digest, format_line
from .extract import TaskExtractor
from .models import Task, User
from .sources.chat import ChatProcessor, IncomingMessage, message_link, text_key
from .vault import Vault

log = logging.getLogger(__name__)

MAX_BUTTONS = 50
HELP = html.escape(
    "Я собираю задания из СДО и из сообщений, которые ты мне пересылаешь, и каждый вечер присылаю сводку.\n\n"
    "Перешли мне сообщение из чата группы (или просто напиши текст) — достану из него задания.\n\n"
    "/digest — сводка сейчас\n"
    "/tasks — все открытые задания\n"
    "/add <текст> — добавить задание вручную, например: /add до 30.10 реферат по истории\n"
    "/cookie <значение> — подключить или обновить СДО\n"
    "/time 21:00 — во сколько присылать сводку\n"
    "/forget — удалить все мои данные"
)
ADMIN_HELP = html.escape(
    "\n\nАдмин:\n"
    "/invite — одноразовая ссылка-приглашение\n"
    "/users — кто пользуется\n"
    "/kick <id> — отключить пользователя и удалить его данные"
)
COOKIE_HELP = (
    "🔑 <b>Как подключить СДО</b>\n\n"
    "1. Открой <b>приватное окно</b> браузера на компьютере и войди на online-edu.mirea.ru.\n"
    "2. Нажми F12 (на Mac — ⌥⌘I) → вкладка <b>Application</b> (в Safari — «Хранилище») → "
    "<b>Cookies</b> → online-edu.mirea.ru.\n"
    "3. Найди строку <code>MoodleSession</code> и скопируй её значение (Value).\n"
    "4. Пришли мне: <code>/cookie значение</code> — сообщение я сразу удалю.\n"
    "5. Закрой приватное окно, <b>не нажимая «Выйти»</b>: выход оборвёт и мою сессию."
)
PRIVACY = (
    "🔒 <b>Что я храню</b>: твои задания и отметки, время сводки и сессию СДО (cookie) — в зашифрованном виде. "
    "Cookie нужна только чтобы читать твои задания, оценки и статусы в СДО; ничего не отправляю и не меняю там.\n"
    "Удалить всё в любой момент: /forget"
)
CLOSED = "Это закрытый бот — нужен пригласительный линк от владельца."
TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")


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
    cookie_help = COOKIE_HELP

    def __init__(self, settings: Settings, db: DB):
        self.s = settings
        self.db = db
        # Прокси — только для запросов к Telegram; СДО и всё остальное идёт напрямую
        session = AiohttpSession(proxy=settings.telegram_proxy) if settings.telegram_proxy else None
        self.bot = Bot(
            settings.bot_token, session=session,
            default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True),
        )
        self._username: str | None = None

    def now(self) -> datetime:
        return datetime.now(self.s.zone)

    async def username(self) -> str:
        if not self._username:
            self._username = (await self.bot.get_me()).username
        return self._username

    async def send_digest(self, user_id: int, full: bool = False) -> None:
        text, shown = build_digest(self.db.list_open(user_id), self.now(), self.s.zone, full=full)
        await self.bot.send_message(user_id, text, reply_markup=done_keyboard(shown, self.s.webapp_url))

    async def notify_new(self, user_id: int, tasks: list[Task]) -> None:
        lines = [format_line(n, t, self.s.zone) for n, t in enumerate(tasks, start=1)]
        title = "🆕 Новое задание" if len(tasks) == 1 else "🆕 Новые задания"
        one = tasks[0].id if len(tasks) == 1 else None
        await self.bot.send_message(
            user_id, f"<b>{title}</b>\n" + "\n".join(lines),
            reply_markup=done_keyboard(tasks, self.s.webapp_url, open_task=one),
        )

    async def say(self, user_id: int, text: str) -> None:
        await self.bot.send_message(user_id, text)

    async def setup_menu_button(self) -> None:
        """Кнопка «Задания» слева от поля ввода у всех — открывает Mini App (чужих API всё равно не пустит)."""
        if url := app_url(self.s.webapp_url):
            await self.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Задания", web_app=WebAppInfo(url=url)))


def from_message(m: Message, user_id: int, explicit: bool) -> IncomingMessage:
    text = m.text or m.caption
    if explicit:
        # Пересланное/написанное в личку: дата оригинала, если это пересылка
        origin = m.forward_origin
        return IncomingMessage(
            user_id=user_id,
            key=text_key(text or ""),
            version="1",
            text=text,
            sent_at=origin.date if origin else m.date,
            source="manual",
            explicit=True,
        )
    return IncomingMessage(
        user_id=user_id,
        key=f"{m.chat.id}:{m.message_id}",
        version=(m.edit_date and str(m.edit_date)) or "0",
        text=text,
        sent_at=m.date,
        sender_id=m.from_user.id if m.from_user else None,
        chat_title=m.chat.title,
        url=message_link(m.chat.id, m.message_id),
    )


def build_group_router(notifier: Notifier, processor: ChatProcessor) -> Router:
    """Если бота добавили в учебную группу из CHAT_IDS: читаем её сам; задания — админу."""
    router = Router()
    groups = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
    allowed = F.chat.id.in_(set(notifier.s.chat_ids))
    admin = notifier.s.owner_id

    @router.my_chat_member(groups)
    async def added(ev: ChatMemberUpdated) -> None:
        if ev.new_chat_member.status in ("member", "administrator") and ev.chat.id not in notifier.s.chat_ids:
            # Чужая группа ничего не тратит, пока админ сам её не разрешит
            await notifier.say(
                admin,
                f"Меня добавили в группу «{html.escape(ev.chat.title or '')}». Чтобы читать её, допиши в .env: "
                f"<code>CHAT_IDS=[{ev.chat.id}]</code> и перезапусти бота.",
            )

    @router.message(groups, allowed)
    @router.edited_message(groups, allowed)
    async def group_message(m: Message) -> None:
        new = await processor.process(from_message(m, admin, explicit=False))
        if new and notifier.s.notify_new:
            await notifier.notify_new(admin, new)

    return router


def build_router(
    notifier: Notifier, db: DB, extractor: TaskExtractor, processor: ChatProcessor, vault: Vault,
    poll_user: Callable[..., Awaitable[int | None]],
) -> Router:
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    admin_id = notifier.s.owner_id

    async def registered(event: Message | CallbackQuery) -> dict | bool:
        """Фильтр: пускает только зарегистрированных и передаёт обработчику user."""
        user = db.get_user(event.from_user.id) if event.from_user else None
        return {"user": user} if user else False

    async def admin(event: Message) -> bool:
        return bool(event.from_user) and event.from_user.id == admin_id

    # ---------- вход по приглашению ----------

    @router.message(CommandStart())
    async def start(m: Message, command: CommandObject) -> None:
        uid = m.from_user.id
        if db.get_user(uid):
            await m.answer(HELP + (ADMIN_HELP if uid == admin_id else ""))
            return
        code = (command.args or "").strip()
        if code and db.use_invite(code, uid):
            db.add_user(uid, m.from_user.full_name)
            log.info("Новый пользователь %s по приглашению", uid)
            await m.answer(f"Привет, {html.escape(m.from_user.first_name)}! 👋\n\n" + HELP)
            await m.answer(PRIVACY)
            await m.answer(COOKIE_HELP)
            await notifier.say(admin_id, f"👤 {html.escape(m.from_user.full_name)} (<code>{uid}</code>) подключился по приглашению")
            return
        await m.answer(CLOSED)

    @router.message(Command("help"), registered)
    async def help_(m: Message, user: User) -> None:
        await m.answer(HELP + (ADMIN_HELP if user.id == admin_id else ""))

    # ---------- задания ----------

    @router.message(Command("digest"), registered)
    async def digest(m: Message, user: User) -> None:
        await notifier.send_digest(user.id)

    @router.message(Command("tasks"), registered)
    async def tasks(m: Message, user: User) -> None:
        await notifier.send_digest(user.id, full=True)

    @router.message(Command("add"), registered)
    async def add(m: Message, command: CommandObject, user: User) -> None:
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
            # Разбор не нашёл задание или упал — сохраняем как есть, без срока
            extracted_tasks = [Task(source="manual", source_id=f"{m.message_id}:0", title=text)]
        else:
            extracted_tasks = [
                Task(source="manual", source_id=f"{m.message_id}:{i}", title=e.title, subject=e.subject, deadline=e.deadline)
                for i, e in enumerate(extracted)
            ]
        new = db.replace_group(user.id, "manual", f"{m.message_id}:", extracted_tasks)
        if new:
            await notifier.notify_new(user.id, new)

    # ---------- настройки ----------

    @router.message(Command("cookie"), registered)
    async def cookie(m: Message, command: CommandObject, user: User) -> None:
        value = (command.args or "").strip()
        # В сообщении секрет — убираем его из истории чата сразу
        try:
            await m.delete()
        except Exception:
            log.warning("Не смог удалить сообщение с cookie — пусть пользователь удалит сам")
        if not value:
            await m.answer(COOKIE_HELP)
            return
        db.set_cookie(user.id, vault.encrypt(value.removeprefix("MoodleSession=")))
        count = await poll_user(db.get_user(user.id), warn=False)
        if count is None:
            await m.answer("❌ С этой cookie СДО не пускает. Проверь, что скопировал MoodleSession уже после входа.")
        else:
            await m.answer(f"✅ СДО подключено, заданий с дедлайнами: {count}. Сводка — /digest")

    @router.message(Command("time"), registered)
    async def time_(m: Message, command: CommandObject, user: User) -> None:
        match = TIME_RE.match((command.args or "").strip())
        if not match:
            await m.answer(f"Сейчас сводка приходит в {user.digest_hour:02d}:{user.digest_minute:02d}. "
                           "Чтобы поменять: /time 21:00")
            return
        hour, minute = int(match[1]), int(match[2])
        db.set_digest_time(user.id, hour, minute)
        await m.answer(f"Готово: сводка каждый день в {hour:02d}:{minute:02d} ({notifier.s.tz}).")

    @router.message(Command("forget"), registered)
    async def forget(m: Message, user: User) -> None:
        if user.id == admin_id:
            await m.answer("Ты админ — твои данные удаляются только вместе с ботом.")
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="🗑 Да, удалить всё", callback_data="forget:yes")
        await m.answer("Удалить твои задания, отметки и сессию СДО? Вернуть будет нельзя, "
                       "а снова пользоваться ботом получится только по новому приглашению.", reply_markup=kb.as_markup())

    @router.callback_query(F.data == "forget:yes", registered)
    async def forget_yes(cb: CallbackQuery, user: User) -> None:
        db.remove_user(user.id)
        await cb.message.edit_text("Готово: все твои данные удалены. Пока! 👋")
        await notifier.say(admin_id, f"👤 <code>{user.id}</code> удалил свои данные (/forget)")

    # ---------- админ ----------

    @router.message(Command("invite"), admin)
    async def invite(m: Message) -> None:
        code = db.create_invite()
        link = f"https://t.me/{await notifier.username()}?start={code}"
        await m.answer(f"Одноразовая ссылка — отправь её одному человеку:\n{link}")

    @router.message(Command("users"), admin)
    async def users(m: Message) -> None:
        lines = []
        for u in db.list_users():
            sdo = "СДО ✅" if u.cookie_enc and not u.auth_warned else ("СДО ⚠️ сессия истекла" if u.cookie_enc else "СДО —")
            lines.append(f"• {html.escape(u.name)} <code>{u.id}</code> · {sdo} · заданий: {len(db.list_open(u.id))}"
                         f" · сводка {u.digest_hour:02d}:{u.digest_minute:02d}")
        await m.answer("<b>Пользователи</b>\n" + "\n".join(lines))

    @router.message(Command("kick"), admin)
    async def kick(m: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip()
        if not arg.isdigit() or int(arg) == admin_id:
            await m.answer("Напиши id из /users: /kick 123456789")
            return
        if db.remove_user(int(arg)):
            await m.answer(f"Пользователь <code>{arg}</code> отключён, его данные удалены.")
        else:
            await m.answer("Такого пользователя нет.")

    # ---------- пересланное и галочки ----------

    @router.message(F.text | F.caption, registered)
    async def forwarded_or_text(m: Message, user: User) -> None:
        # Всё, что не команда: пересланное из группы или написанное самим пользователем
        if (m.text or "").startswith("/"):
            await m.answer("Не знаю такой команды. /help")
            return
        msg = from_message(m, user.id, explicit=True)
        if db.is_seen(f"{user.id}|{msg.key}", msg.version):
            await m.answer("Это сообщение я уже разбирал.")
            return
        new = await processor.process(msg)
        if new is None:
            await m.answer("Разбор сейчас недоступен — пришли ещё раз позже или добавь через /add.")
        elif new:
            await notifier.notify_new(user.id, new)
        else:
            await m.answer("Не нашёл здесь задания. Если оно есть — /add текст задания.")

    @router.callback_query(F.data.startswith("done:"), registered)
    async def done(cb: CallbackQuery, user: User) -> None:
        task_id = int(cb.data.split(":", 1)[1])
        task = db.get(user.id, task_id)
        if task and db.mark_done(user.id, task_id):
            await cb.answer(f"Сделано: {task.title[:60]}")
        else:
            await cb.answer("Уже отмечено")

    # ---------- все остальные ----------

    @router.message()
    async def stranger(m: Message) -> None:
        await m.answer(CLOSED)

    @router.callback_query()
    async def stranger_cb(cb: CallbackQuery) -> None:
        await cb.answer(CLOSED, show_alert=True)

    return router
