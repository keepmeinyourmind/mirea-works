"""Разбор страниц СДО. Базовые образцы — настоящая разметка online-edu.mirea.ru (tests/fixtures);
сданные/оценённые варианты получены заменой строк на стандартные строки Moodle."""

from pathlib import Path

import pytest

from study_bot.sources.moodle_pages import parse_assign, parse_page, parse_quiz, points_from_text

FIX = Path(__file__).parent / "fixtures"
ASSIGN = (FIX / "assign_not_submitted.html").read_text()
QUIZ = (FIX / "quiz_info.html").read_text()


def with_status(answer: str, grading: str, grade_row: str = "") -> str:
    html = ASSIGN.replace("Ответы на задание еще не представлены", answer).replace(">Не оценено<", f">{grading}<")
    return html.replace("</tbody>", grade_row + "</tbody>")


def test_real_assign_page_not_submitted():
    st = parse_assign(ASSIGN)
    assert (st.points, st.status, st.grade) == (7, "none", None)  # «Проходной балл 3,0» не путаем с максимумом


@pytest.mark.parametrize(
    "answer, grading, grade_row, status, grade",
    [
        ("Черновик (не отправлен)", "Не оценено", "", "draft", None),
        ("Отправлено для оценивания", "Не оценено", "", "submitted", None),
        ("Отправлено для оценивания", "Оценено", "<tr><th>Оценка</th><td>4,00 / 7,00</td></tr>", "graded", 4),
        ("Отправлено для оценивания", "Оценено", "<tr><th>Оценка</th><td>6,50 / 7,00</td></tr>", "graded", 6.5),
    ],
)
def test_assign_statuses(answer, grading, grade_row, status, grade):
    st = parse_assign(with_status(answer, grading, grade_row))
    assert (st.status, st.grade, st.points) == (status, grade, 7)


def test_real_quiz_page_without_attempts():
    st = parse_quiz(QUIZ)
    assert (st.status, st.attempts_total, st.attempts_used, st.grade) == ("quiz", 3, 0, None)
    assert st.points == 4  # «Проходная оценка: 3,00 из 4,00» → максимум 4


def test_quiz_points_prefer_teacher_description():
    # Преподаватель пишет баллы БРС в описании — они важнее внутренней шкалы теста
    st = parse_quiz(QUIZ, description="20 вопросов на 30 минут. Максимальный балл за тест - 10 баллов")
    assert st.points == 10


def test_quiz_with_finished_attempt_and_final_grade():
    attempts = (
        "<table class='quizattemptsummary'><tr><th>Попытка</th><th>Состояние</th><th>Оценка / 10,00</th></tr>"
        "<tr><td>1</td><td>Завершенные Отправлено 20 октября</td><td>7,00</td></tr></table>"
        "<div id='feedback'><h3>Ваша итоговая оценка за этот тест: 7,00/10,00.</h3></div>"
    )
    st = parse_quiz(QUIZ + attempts)
    assert (st.attempts_used, st.attempts_total, st.grade, st.points) == (1, 3, 7, 10)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Максимальный балл за тест - 10 баллов", 10),
        ("Максимальный балл за тест: 5 баллов", 5),
        ("КАК ПРОХОДИТ ТЕСТ 20 вопросов 30 минут 1 попытка Максимальный балл за тест — 6 баллов", 6),
        ("Без баллов", None),
        (None, None),
    ],
)
def test_points_from_description(text, expected):
    assert points_from_text(text) == expected


def test_parse_page_dispatches_by_url():
    assert parse_page("https://online-edu.mirea.ru/mod/quiz/view.php?id=1", QUIZ).status == "quiz"
    assert parse_page("https://online-edu.mirea.ru/mod/assign/view.php?id=1", ASSIGN).points == 7
    assert parse_page("https://online-edu.mirea.ru/mod/page/view.php?id=1", "", "Максимальный балл 3").points == 3
