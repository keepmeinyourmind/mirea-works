from study_bot.bot import app_url, done_keyboard
from study_bot.models import Task


def tasks(n):
    return [Task(source="tg", source_id=str(i), title=str(i), id=i) for i in range(1, n + 1)]


def test_app_url_requires_https():
    assert app_url("") is None
    assert app_url("http://localhost:8080") is None  # Telegram не примет кнопку на http
    assert app_url("https://studybot.duckdns.org") == "https://studybot.duckdns.org/"
    assert app_url("https://studybot.duckdns.org/", 5) == "https://studybot.duckdns.org/?task=5"


def test_keyboard_without_webapp_has_only_done_buttons():
    kb = done_keyboard(tasks(7))
    assert [len(r) for r in kb.inline_keyboard] == [5, 2]
    assert kb.inline_keyboard[0][0].callback_data == "done:1"


def test_keyboard_with_webapp_adds_open_button():
    kb = done_keyboard(tasks(2), "https://app.example.org")
    last = kb.inline_keyboard[-1]
    assert len(last) == 1 and last[0].web_app.url == "https://app.example.org/"
    kb = done_keyboard(tasks(1), "https://app.example.org", open_task=1)
    assert kb.inline_keyboard[-1][0].web_app.url == "https://app.example.org/?task=1"


def test_empty_digest_with_webapp_still_has_open_button():
    kb = done_keyboard([], "https://app.example.org")
    assert kb.inline_keyboard[0][0].text == "📋 Открыть приложение"
    assert done_keyboard([]) is None
