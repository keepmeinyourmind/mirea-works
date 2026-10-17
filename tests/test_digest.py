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


def test_nearest_far_tasks_are_shown_even_when_week_is_empty():
    # Как 17.10: на неделе ничего, ближайшее — через 8 дней. Сводка не должна быть пустой
    tasks = [task("КР 3", eod(8)), task("Тест", eod(8)), task("Курсовая", eod(40))]
    text, shown = build_digest(tasks, NOW, MSK)
    assert "Дальше" in text
    assert [t.title for t in shown] == ["КР 3", "Тест", "Курсовая"]
    assert "Ещё" not in text


def test_only_nearest_five_far_tasks_in_daily_digest():
    tasks = [task(f"Задание {d}", eod(d)) for d in range(10, 18)]  # 8 штук дальше недели
    text, shown = build_digest(tasks, NOW, MSK)
    assert [t.title for t in shown] == [f"Задание {d}" for d in range(10, 15)]
    assert "Ещё 3 позже — /tasks" in text

    text, shown = build_digest(tasks, NOW, MSK, full=True)
    assert len(shown) == 8 and "Ещё" not in text


def test_html_is_escaped():
    text, _ = build_digest([task("<script>", subject="A&B")], NOW, MSK)
    assert "&lt;script&gt;" in text and "A&amp;B" in text


def test_deadline_format_hides_default_time():
    assert format_deadline(eod(1), MSK) == "18.10 (вс)"
    assert format_deadline(datetime(2026, 10, 20, 10, 30, tzinfo=MSK), MSK) == "20.10 (вт) 10:30"


def test_points_and_draft_in_digest_line():
    t = Task(source="portal", source_id="1", title="КР 3", deadline=eod(2), points=7, status="draft")
    text, _ = build_digest([t], NOW, MSK)
    assert "+7 б." in text and "черновик не отправлен" in text
    t.points = 4.5
    assert "+4,5 б." in build_digest([t], NOW, MSK)[0]
