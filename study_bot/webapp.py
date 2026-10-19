"""HTTP API и статика для Telegram Mini App — в том же процессе, что и бот.

Вход: Telegram передаёт приложению initData, подписанные токеном бота. Фронтенд шлёт их в заголовке
`Authorization: tma <initData>`, мы проверяем подпись, свежесть и что это владелец (OWNER_ID).
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from .config import Settings
from .db import DB
from .models import Task

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "webapp_static"
MAX_INIT_AGE = 48 * 3600  # initData живут, пока открыто приложение; старше двух суток — переоткрыть
DONE_DAYS = 14  # сколько дней показывать сделанное в секции «Сделано»
MAX_TITLE = 300

SETTINGS = web.AppKey("settings", Settings)
STORE = web.AppKey("db", DB)
USER = web.RequestKey("user_id", int)  # кладётся в request проверкой входа


class Unauthorized(web.HTTPUnauthorized):
    def __init__(self, reason: str):
        super().__init__(text=reason)


def check_init_data(init_data: str, settings: Settings, now: float | None = None) -> int:
    """Проверяет подпись и свежесть; возвращает Telegram id пользователя или бросает Unauthorized."""
    try:
        data = safe_parse_webapp_init_data(settings.bot_token, init_data)
    except ValueError:
        raise Unauthorized("Неверная подпись Telegram")
    if (now or time.time()) - data.auth_date.timestamp() > MAX_INIT_AGE:
        raise Unauthorized("Сессия приложения устарела — открой его заново")
    if not data.user:
        raise Unauthorized("Открой приложение из Telegram")
    return data.user.id


@web.middleware
async def auth(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    s = request.app[SETTINGS]
    if s.webapp_dev_no_auth:
        user_id = s.owner_id  # локальная разработка: смотрим как админ
    else:
        header = request.headers.get("Authorization", "")
        if not header.startswith("tma "):
            raise Unauthorized("Открой приложение из Telegram")
        user_id = check_init_data(header[4:], s)
    if not request.app[STORE].get_user(user_id):
        raise web.HTTPForbidden(text="Это закрытый бот — нужен пригласительный линк от владельца")
    request[USER] = user_id
    return await handler(request)


def task_json(t: Task, s: Settings) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "subject": t.subject,
        "deadline": t.deadline.astimezone(s.zone).isoformat() if t.deadline else None,
        "source": t.source,
        "url": t.url,
        "done": t.done,
        "auto_done": t.auto_done,
        "edited": t.edited,
        "points": _num(t.points),
        "status": t.status,
        "grade": _num(t.grade),
        "attempts_used": t.attempts_used,
        "attempts_total": t.attempts_total,
        "checked_at": t.checked_at.isoformat() if t.checked_at else None,
    }


def _num(v: float | None) -> float | int | None:
    return int(v) if isinstance(v, float) and v.is_integer() else v


def parse_fields(body: dict, s: Settings, partial: bool) -> dict:
    """Проверка полей из приложения. Ошибка → 400 с понятным текстом."""
    out: dict = {}

    def bad(msg: str):
        raise web.HTTPBadRequest(text=msg)

    if "title" in body or not partial:
        title = str(body.get("title") or "").strip()
        if not title:
            bad("Напиши, что нужно сделать")
        out["title"] = title[:MAX_TITLE]
    if "subject" in body:
        out["subject"] = (str(body["subject"]).strip() or None) if body["subject"] else None
    if "deadline" in body:
        d = body["deadline"]
        if d:
            try:
                dt = datetime.fromisoformat(d)
            except (TypeError, ValueError):
                bad("Не понял срок")
            out["deadline"] = dt if dt.tzinfo else dt.replace(tzinfo=s.zone)
        else:
            out["deadline"] = None
    if "points" in body:
        p = body["points"]
        if p in (None, ""):
            out["points"] = None
        else:
            try:
                p = float(p)
            except (TypeError, ValueError):
                bad("Баллы — это число")
            if not 0 <= p <= 1000:
                bad("Баллы должны быть от 0 до 1000")
            out["points"] = int(p) if p.is_integer() else p
    if "url" in body:
        u = str(body["url"] or "").strip()
        if u and not (u.startswith("https://") or u.startswith("http://")):
            bad("Ссылка должна начинаться с https://")
        out["url"] = u or None
    if "done" in body:
        out["done"] = bool(body["done"])
    return out


async def list_tasks(request: web.Request) -> web.Response:
    s, db = request.app[SETTINGS], request.app[STORE]
    since = datetime.now(s.zone) - timedelta(days=DONE_DAYS)
    uid = request[USER]
    tasks = db.list_visible(uid, done_since=since)
    return web.json_response({
        "tasks": [task_json(t, s) for t in tasks],
        "subjects": sorted(set(db.subjects(uid))),
        "now": datetime.now(s.zone).isoformat(),
    })


async def create_task(request: web.Request) -> web.Response:
    s, db = request.app[SETTINGS], request.app[STORE]
    f = parse_fields(await _json(request), s, partial=False)
    t = db.create_manual(request[USER], f["title"], f.get("subject"), f.get("deadline"), f.get("points"), f.get("url"))
    return web.json_response(task_json(t, s), status=201)


async def update_task(request: web.Request) -> web.Response:
    s, db = request.app[SETTINGS], request.app[STORE]
    uid, task_id = request[USER], int(request.match_info["id"])
    if not db.get(uid, task_id):
        raise web.HTTPNotFound(text="Задание не найдено")
    f = parse_fields(await _json(request), s, partial=True)
    if "done" in f:
        db.set_done(uid, task_id, f.pop("done"))
    t = db.update_fields(uid, task_id, f) if f else db.get(uid, task_id)
    return web.json_response(task_json(t, s))


async def delete_task(request: web.Request) -> web.Response:
    if not request.app[STORE].remove(request[USER], int(request.match_info["id"])):
        raise web.HTTPNotFound(text="Задание не найдено")
    return web.Response(status=204)


def static_version() -> str:
    """Меняется при изменении app.js/style.css — встроенный браузер Telegram любит держать их в кэше."""
    return str(int(max((STATIC / f).stat().st_mtime for f in ("app.js", "style.css"))))


async def index(request: web.Request) -> web.Response:
    v = static_version()
    html = (STATIC / "index.html").read_text().replace("/static/app.js", f"/static/app.js?v={v}")
    html = html.replace("/static/style.css", f"/static/style.css?v={v}")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def _json(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="Ожидался JSON")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Ожидался JSON-объект")
    return body


def build_app(settings: Settings, db: DB) -> web.Application:
    app = web.Application(middlewares=[auth])
    app[SETTINGS] = settings
    app[STORE] = db
    app.router.add_get("/", index)
    app.router.add_get("/api/tasks", list_tasks)
    app.router.add_post("/api/tasks", create_task)
    app.router.add_patch(r"/api/tasks/{id:\d+}", update_task)
    app.router.add_delete(r"/api/tasks/{id:\d+}", delete_task)
    app.router.add_static("/static", STATIC)
    return app


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


async def start_webapp(settings: Settings, db: DB) -> web.AppRunner:
    if settings.webapp_dev_no_auth and settings.webapp_host not in LOCAL_HOSTS:
        # Без проверки Telegram API открыт любому — допустимо только на своём компьютере
        raise RuntimeError("WEBAPP_DEV_NO_AUTH=true разрешён только с WEBAPP_HOST=127.0.0.1")
    runner = web.AppRunner(build_app(settings, db))
    await runner.setup()
    await web.TCPSite(runner, settings.webapp_host, settings.webapp_port).start()
    if settings.webapp_dev_no_auth:
        log.warning("WEBAPP_DEV_NO_AUTH: API открыт без проверки Telegram — только для локальной разработки!")
    log.info("Mini App: http://%s:%s", settings.webapp_host, settings.webapp_port)
    return runner
