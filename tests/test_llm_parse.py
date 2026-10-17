from datetime import datetime, time
from zoneinfo import ZoneInfo

from study_bot.extract.llm import build_prompt, parse_tasks

MSK = ZoneInfo("Europe/Moscow")


def test_parse_full_task():
    data = {"tasks": [{"subject": "ОС", "title": "Лаба 2", "deadline_date": "2026-10-25", "deadline_time": "10:30"}]}
    [t] = parse_tasks(data, MSK)
    assert t.subject == "ОС"
    assert t.deadline == datetime(2026, 10, 25, 10, 30, tzinfo=MSK)


def test_date_without_time_means_end_of_day():
    data = {"tasks": [{"subject": None, "title": "Реферат", "deadline_date": "2026-11-01", "deadline_time": None}]}
    [t] = parse_tasks(data, MSK)
    assert t.deadline.time() == time(23, 59)
    assert t.subject is None


def test_bad_date_keeps_task_without_deadline():
    data = {"tasks": [{"subject": "", "title": "ДЗ", "deadline_date": "к пятнице", "deadline_time": "утром"}]}
    [t] = parse_tasks(data, MSK)
    assert t.deadline is None
    assert t.subject is None  # пустая строка → None


def test_empty_titles_are_dropped():
    assert parse_tasks({"tasks": [{"subject": None, "title": "  ", "deadline_date": None, "deadline_time": None}]}, MSK) == []
    assert parse_tasks({"tasks": []}, MSK) == []


def test_prompt_contains_local_date_and_weekday():
    # 17.10.2026 — суббота; 21:30 UTC = 00:30 МСК следующего дня
    sent = datetime(2026, 10, 17, 21, 30, tzinfo=ZoneInfo("UTC"))
    prompt = build_prompt("к пятнице лаба", sent, "ИКБО-01-24", MSK)
    assert "2026-10-18 (воскресенье)" in prompt
    assert "ИКБО-01-24" in prompt
