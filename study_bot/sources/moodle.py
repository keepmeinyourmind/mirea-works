"""СДО МИРЭА (online-edu.mirea.ru) — это Moodle.

Мобильный веб-сервис для SSO там отключён, поэтому ходим тем же путём, что и сама страница:
cookie сессии MoodleSession → sesskey со страницы /my/ → AJAX lib/ajax/service.php.

core_calendar_get_action_events_by_timesort отдаёт задания и тесты, по которым ещё нужно что-то
сделать. Сданное оттуда пропадает — так мы узнаём, что задание закрыто.

Сессия живёт, пока ей пользуются: частый опрос (раз в 30 мин) не даёт ей истечь.
Когда всё же протухнет — PortalAuthError, бот попросит прислать новую cookie через /cookie.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..models import Task
from .moodle_pages import PortalStatus, parse_page, points_from_text
from .portal import PortalAuthError

PAGE = 50
AUTH_ERRORS = {"servicerequireslogin", "invalidsesskey", "requireloginerror", "sessionerroruser"}
SESSKEY_RE = re.compile(r'"sesskey":"([^"]+)"')


def clean_subject(fullname: str | None) -> str | None:
    # "Фронтенд и бэкенд разработка_Экзамен (часть 1/2) [I.26-27]" → "Фронтенд и бэкенд разработка"
    if not fullname:
        return None
    return fullname.split("_", 1)[0].strip() or None


def event_to_task(e: dict) -> Task:
    return Task(
        source="portal",
        source_id=str(e["id"]),
        title=(e.get("activityname") or e["name"]).strip(),
        subject=clean_subject((e.get("course") or {}).get("fullname")),
        deadline=datetime.fromtimestamp(e["timesort"], tz=timezone.utc),
        url=e.get("url"),
        # «Максимальный балл за тест — 10 баллов» часто пишут прямо в описании
        points=points_from_text(_strip_html(e.get("description"))),
    )


def _strip_html(html: str | None) -> str | None:
    return re.sub(r"<[^>]+>", " ", html) if html else None


def read_legacy_cookie(path: Path) -> str:
    """Cookie из однопользовательской версии (data/moodle_session.txt) — только для переноса в базу."""
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


class MoodleSessionPortal:
    """Один объект на всех пользователей: cookie передаётся в каждый вызов."""

    def __init__(self, base_url: str, lookback_days: int, transport: httpx.AsyncBaseTransport | None = None):
        self.base = base_url.rstrip("/")
        self.lookback_days = lookback_days
        self.transport = transport

    def _client(self, cookie: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base,
            cookies={"MoodleSession": cookie},
            timeout=30,
            follow_redirects=False,  # редирект на логин = сессия протухла
            transport=self.transport,
        )

    async def _sesskey(self, client: httpx.AsyncClient) -> str:
        r = await client.get("/my/")
        if r.is_redirect or r.status_code in (401, 403):
            raise PortalAuthError("сессия портала истекла")
        r.raise_for_status()
        m = SESSKEY_RE.search(r.text)
        if not m:
            raise PortalAuthError("на странице нет sesskey — похоже, вход не выполнен")
        return m.group(1)

    async def _ajax(self, client: httpx.AsyncClient, sesskey: str, method: str, args: dict) -> dict:
        r = await client.post(
            f"/lib/ajax/service.php?sesskey={sesskey}&info={method}",
            content=json.dumps([{"index": 0, "methodname": method, "args": args}]),
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        [res] = r.json()
        if res.get("error"):
            exc = res.get("exception") or {}
            if exc.get("errorcode") in AUTH_ERRORS:
                raise PortalAuthError(exc.get("message") or exc["errorcode"])
            raise RuntimeError(f"Moodle {method}: {exc.get('errorcode')}: {exc.get('message')}")
        return res["data"]

    async def fetch(self, cookie: str | None) -> list[Task]:
        if not cookie:
            raise PortalAuthError("cookie портала не задана")
        since = int(datetime.now(timezone.utc).timestamp()) - self.lookback_days * 86400
        async with self._client(cookie) as client:
            sesskey = await self._sesskey(client)
            tasks: list[Task] = []
            args: dict = {"timesortfrom": since, "limitnum": PAGE}
            while True:
                data = await self._ajax(client, sesskey, "core_calendar_get_action_events_by_timesort", args)
                events = data.get("events", [])
                tasks += [event_to_task(e) for e in events]
                if len(events) < PAGE:
                    return tasks
                args = {**args, "aftereventid": data["lastid"]}

    async def check(self, cookie: str | None, tasks: list[Task]) -> dict[int, PortalStatus]:
        """Статус и баллы со страниц заданий. Один клиент на пачку — страницы грузим по очереди."""
        if not cookie:
            raise PortalAuthError("cookie портала не задана")
        result: dict[int, PortalStatus] = {}
        async with self._client(cookie) as client:
            for t in tasks:
                if not t.url or not t.url.startswith(self.base):
                    continue
                r = await client.get(t.url.removeprefix(self.base))
                if r.is_redirect:
                    raise PortalAuthError("сессия портала истекла")
                if r.status_code != 200:
                    continue
                result[t.id] = parse_page(t.url, r.text)
        return result
