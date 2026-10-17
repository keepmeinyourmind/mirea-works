"""API Mini App: подпись Telegram, владелец, CRUD заданий, правила для заданий портала."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from study_bot.config import Settings
from study_bot.db import DB
from study_bot.models import Task
from study_bot.webapp import build_app

TOKEN = "123456:TEST-token"
OWNER = 777


def sign(user_id: int, auth_date: int | None = None, token: str = TOKEN) -> str:
    """initData так, как их подписывает Telegram (core.telegram.org/bots/webapps)."""
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": "Я"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
async def api():
    s = Settings(_env_file=None, bot_token=TOKEN, owner_id=OWNER)
    db = DB(":memory:")
    client = TestClient(TestServer(build_app(s, db)))
    await client.start_server()
    client.db = db
    client.auth = {"Authorization": "tma " + sign(OWNER)}
    yield client
    await client.close()


async def test_requires_telegram_signature(api):
    assert (await api.get("/api/tasks")).status == 401
    assert (await api.get("/api/tasks", headers={"Authorization": "tma " + sign(OWNER, token="1:other")})).status == 401


async def test_rejects_other_users_and_stale_data(api):
    assert (await api.get("/api/tasks", headers={"Authorization": "tma " + sign(42)})).status == 403
    old = int(time.time()) - 3 * 86400
    assert (await api.get("/api/tasks", headers={"Authorization": "tma " + sign(OWNER, old)})).status == 401


async def test_create_edit_done_delete_manual_task(api):
    r = await api.post("/api/tasks", json={"title": "Доклад", "deadline": "2026-10-20T10:00", "points": "4.5",
                                           "url": "https://example.org"}, headers=api.auth)
    assert r.status == 201
    t = await r.json()
    assert (t["source"], t["points"], t["deadline"]) == ("manual", 4.5, "2026-10-20T10:00:00+03:00")

    r = await api.patch(f"/api/tasks/{t['id']}", json={"title": "Доклад v2", "done": True}, headers=api.auth)
    t2 = await r.json()
    assert (t2["title"], t2["done"]) == ("Доклад v2", True)

    data = await (await api.get("/api/tasks", headers=api.auth)).json()
    assert [x["title"] for x in data["tasks"]] == ["Доклад v2"]  # сделанное тоже отдаём — для секции «Сделано»

    assert (await api.delete(f"/api/tasks/{t['id']}", headers=api.auth)).status == 204
    assert (await (await api.get("/api/tasks", headers=api.auth)).json())["tasks"] == []


@pytest.mark.parametrize(
    "body, message",
    [
        ({"title": "  "}, "Напиши, что нужно сделать"),
        ({"title": "a", "points": "много"}, "Баллы — это число"),
        ({"title": "a", "points": -1}, "Баллы должны быть от 0 до 1000"),
        ({"title": "a", "url": "ftp://x"}, "Ссылка должна начинаться с https://"),
        ({"title": "a", "deadline": "завтра"}, "Не понял срок"),
    ],
)
async def test_validation_messages(api, body, message):
    r = await api.post("/api/tasks", json=body, headers=api.auth)
    assert r.status == 400
    assert await r.text() == message


async def test_editing_portal_task_protects_it_and_delete_hides_it(api):
    api.db.upsert(Task(source="portal", source_id="9", title="КР 3", url="https://online-edu.mirea.ru/x"))
    tid = api.db.list_open()[0].id
    r = await api.patch(f"/api/tasks/{tid}", json={"deadline": "2026-10-28T12:00"}, headers=api.auth)
    assert (await r.json())["edited"] is True

    assert (await api.delete(f"/api/tasks/{tid}", headers=api.auth)).status == 204
    api.db.upsert(Task(source="portal", source_id="9", title="КР 3"))  # следующий опрос СДО
    assert (await (await api.get("/api/tasks", headers=api.auth)).json())["tasks"] == []


async def test_unknown_task_is_404(api):
    assert (await api.patch("/api/tasks/999", json={"done": True}, headers=api.auth)).status == 404
    assert (await api.delete("/api/tasks/999", headers=api.auth)).status == 404


async def test_dev_mode_refuses_public_host():
    from study_bot.webapp import start_webapp

    s = Settings(_env_file=None, bot_token=TOKEN, owner_id=OWNER, webapp_dev_no_auth=True, webapp_host="0.0.0.0")
    with pytest.raises(RuntimeError, match="только с WEBAPP_HOST=127.0.0.1"):
        await start_webapp(s, DB(":memory:"))
