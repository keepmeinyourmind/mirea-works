from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from study_bot.extract.rules import SubjectMatcher, parse_rules, pick_deadline

MSK = ZoneInfo("Europe/Moscow")
SENT = datetime(2026, 10, 17, 12, 0, tzinfo=MSK)  # суббота

COURSES = [
    "Фронтенд и бэкенд разработка",
    "Базы данных для индустриальных задач",
    "Технологии индустриального программирования",
    "Операционные системы",
]


def deadline_day(text: str) -> date | None:
    d = pick_deadline(text, SENT, MSK)
    return d.date() if d else None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("до 25.10 сдать лабу", date(2026, 10, 25)),
        ("срок 25/10", date(2026, 10, 25)),
        ("до 01.11.2026", date(2026, 11, 1)),
        ("до 01.11.26", date(2026, 11, 1)),
        ("к 15.01 курсовая", date(2027, 1, 15)),  # январь в октябре — следующий год
        ("25 октября контрольная", date(2026, 10, 25)),
        ("до 3 ноября реферат", date(2026, 11, 3)),
        ("к пятнице лаба", date(2026, 10, 23)),
        ("в пт тест", date(2026, 10, 23)),
        ("к субботе", date(2026, 10, 24)),  # сказано в субботу — значит следующая
        ("к понедельнику", date(2026, 10, 19)),
        ("сегодня до вечера", date(2026, 10, 17)),
        ("сдать завтра", date(2026, 10, 18)),
        ("послезавтра коллоквиум", date(2026, 10, 19)),
        ("через 3 дня", date(2026, 10, 20)),
        ("через неделю", date(2026, 10, 24)),
        ("через 2 недели", date(2026, 10, 31)),
        ("лабу перенесли с пятницы на вторник", date(2026, 10, 20)),  # перенос — берём последний срок
        ("лаба 2.1 без срока", None),  # нумерация, а не 2 января
        ("номер 3.5", None),
        ("упр. 1.2 и задача 4.3", None),
        ("31.02 — такой даты нет", None),
        ("просто домашка", None),
    ],
)
def test_deadlines(text, expected):
    assert deadline_day(text) == expected


def test_explicit_time():
    assert pick_deadline("тест до 25.10 в 10:30", SENT, MSK).time() == time(10, 30)
    assert pick_deadline("тест до 25.10", SENT, MSK).time() == time(23, 59)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("КР по фронтенду в пятницу", "Фронтенд и бэкенд разработка"),
        ("Лаба по базам данных", "Базы данных для индустриальных задач"),
        ("ДЗ по ОС", "Операционные системы"),  # сокращение из SUBJECT_ALIASES
        ("матан номера 1-10", "Математический анализ"),
        ("лаба по операционным системам", "Операционные системы"),
        ("контрольная по истории", None),
    ],
)
def test_subjects(text, expected):
    matcher = SubjectMatcher(COURSES, aliases={"ос": "Операционные системы"})
    assert matcher.match(text) == expected


def test_alias_short_words_only_whole():
    matcher = SubjectMatcher([], aliases={"ос": "Операционные системы"})
    assert matcher.match("осталось две недели") is None


def test_single_message_task():
    [t] = parse_rules("Ребята, напоминаю: до 25.10 сдать лабу 2 по фронтенду", SENT, MSK, SubjectMatcher(COURSES))
    assert t.title == "Напоминаю: до 25.10 сдать лабу 2 по фронтенду"
    assert t.subject == "Фронтенд и бэкенд разработка"
    assert t.deadline.date() == date(2026, 10, 25)


def test_list_message_gives_task_per_item():
    text = "ДЗ до пятницы:\n1. Лаба 3 по базам данных\n2. Тест по ОС до 20.10\n3. Реферат"
    tasks = parse_rules(text, SENT, MSK, SubjectMatcher(COURSES, aliases={"ос": "Операционные системы"}))
    assert [t.title for t in tasks] == ["Лаба 3 по базам данных", "Тест по ОС до 20.10", "Реферат"]
    assert [t.deadline.date() for t in tasks] == [date(2026, 10, 23), date(2026, 10, 20), date(2026, 10, 23)]
    assert tasks[0].subject == "Базы данных для индустриальных задач"
    assert tasks[1].subject == "Операционные системы"


def test_chatter_gives_nothing():
    assert parse_rules("Кто идёт в столовку?", SENT, MSK, SubjectMatcher(COURSES)) == []
    assert parse_rules("", SENT, MSK, SubjectMatcher(COURSES)) == []


def test_long_title_is_truncated():
    [t] = parse_rules("Сдать " + "очень " * 40 + "длинное задание", SENT, MSK, SubjectMatcher([]))
    assert len(t.title) <= 100 and t.title.endswith("…")
