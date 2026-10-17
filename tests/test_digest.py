from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from study_bot.digest import build_digest, format_deadline
from study_bot.models import Task

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 17, 20, 0, tzinfo=MSK)  # суббота, время сводки


def task(title: str, deadline: datetime | None = None, subject: str | None = None) -> Task:
    return Task(source="tg", source_id=title, title=title, subject=subject, deadline=deadline)


def eod(days: int) -> datetime:
    return (NOW + timedelta(days=days)).replace(hour=23, minute=59)


def test_empty_digest():
    text, shown = build_digest([], NOW, MSK)
    assert text == "Открытых заданий нет 🎉"
    assert shown == []


def test_sections_and_numbering():
    tasks = [
        task("без срока"),
        task("через 3 дня", eod(3)),
        task("завтра", eod(1)),
        task("просрочено", NOW - timedelta(hours=2)),
        task("сегодня", eod(0)),
    ]
    text, shown = build_digest(tasks, NOW, MSK)
    assert [t.title for t in shown] == ["просрочено", "сегодня", "завтра", "через 3 дня", "без срока"]
    order = [text.index(h) for h in ["Просрочено", "Сегодня", "Завтра", "На неделе", "Без срока"]]
    assert order == sorted(order)
    assert "1. просрочено" in text and "5. без срока" in text


def test_far_deadlines_hidden_in_daily_digest_but_shown_in_full():
    tasks = [task("скоро", eod(2)), task("курсовая", eod(30))]
    text, shown = build_digest(tasks, NOW, MSK)
    assert [t.title for t in shown] == ["скоро"]
    assert "Ещё 1 с дальним сроком" in text

    text, shown = build_digest(tasks, NOW, MSK, full=True)
    assert [t.title for t in shown] == ["скоро", "курсовая"]
    assert "Позже" in text


def test_only_far_tasks_is_not_reported_as_empty():
    text, _ = build_digest([task("курсовая", eod(30))], NOW, MSK)
    assert "нет" not in text
    assert "Ещё 1" in text


def test_html_is_escaped():
    text, _ = build_digest([task("<script>", subject="A&B")], NOW, MSK)
    assert "&lt;script&gt;" in text and "A&amp;B" in text


def test_deadline_format_hides_default_time():
    assert format_deadline(eod(1), MSK) == "18.10 (вс)"
    assert format_deadline(datetime(2026, 10, 20, 10, 30, tzinfo=MSK), MSK) == "20.10 (вт) 10:30"
