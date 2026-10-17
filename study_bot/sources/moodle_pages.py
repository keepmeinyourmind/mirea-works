"""Разбор страниц заданий и тестов СДО: максимальный балл, статус ответа, оценка, попытки.

API этого не отдаёт (mod_assign_* и gradereport_* в МИРЭА закрыты), поэтому читаем HTML.
Строки — стандартные русские строки Moodle, сверены со страницами online-edu.mirea.ru.
"""

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

NUM = r"(\d+(?:[,.]\d+)?)"


def _num(s: str) -> float:
    v = float(s.replace(",", "."))
    return int(v) if v.is_integer() else v


@dataclass
class PortalStatus:
    points: float | None = None
    status: str = "none"  # none | draft | submitted | graded | quiz
    grade: float | None = None
    attempts_used: int | None = None
    attempts_total: int | None = None


class _Rows(HTMLParser):
    """Собирает пары «заголовок — значение» из строк таблиц и весь текст страницы."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("br", "p", "div", "li"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        self.text.append(data)


def _parse(html: str) -> tuple[dict[str, str], str]:
    p = _Rows()
    p.feed(html)
    pairs = {r[0]: r[1] for r in p.rows if len(r) >= 2}
    text = re.sub(r"[ \t\xa0]+", " ", unescape("".join(p.text)))
    return pairs, text


def _find(pairs: dict[str, str], prefix: str) -> str | None:
    return next((v for k, v in pairs.items() if k.lower().startswith(prefix.lower())), None)


def parse_assign(html: str) -> PortalStatus:
    pairs, _ = _parse(html)
    st = PortalStatus()
    if m := re.search(NUM, _find(pairs, "Максимальная оценка") or ""):
        st.points = _num(m[1])

    answer = (_find(pairs, "Состояние ответа") or "").lower()
    grading = (_find(pairs, "Состояние оценивания") or "").lower()
    if "черновик" in answer:
        st.status = "draft"
    elif "отправлено" in answer or ("представлен" in answer and "не представлен" not in answer):
        st.status = "submitted"

    # Оценка в отзыве: «4,00 / 5,00»
    grade_cell = _find(pairs, "Оценка") if "оценено" in grading and "не оценено" not in grading else None
    if grade_cell and (m := re.search(NUM + r"\s*/\s*" + NUM, grade_cell)):
        st.status, st.grade = "graded", _num(m[1])
        st.points = st.points or _num(m[2])
    elif "оценено" in grading and "не оценено" not in grading:
        st.status = "graded"
    return st


POINTS_IN_TEXT = re.compile(r"максимальн\w*\s+балл\w*[^0-9]{0,25}" + NUM, re.I)


def points_from_text(text: str | None) -> float | None:
    """«Максимальный балл за тест — 10 баллов» в описании задания/теста."""
    if text and (m := POINTS_IN_TEXT.search(text)):
        return _num(m[1])
    return None


def parse_quiz(html: str, description: str | None = None) -> PortalStatus:
    pairs, text = _parse(html)
    st = PortalStatus(status="quiz")
    if m := re.search(r"Разрешено попыток:\s*(\d+)", text):
        st.attempts_total = int(m[1])
    # Итог за тест: «Ваша итоговая оценка за этот тест: 7,00/10,00» или «Наивысшая оценка: 7,00 / 10,00»
    if m := re.search(r"(?:итоговая|наивысшая|высшая)\s+оценка[^0-9]{0,30}" + NUM + r"\s*/\s*" + NUM, text, re.I):
        st.grade, st.points = _num(m[1]), _num(m[2])
    st.points = st.points or points_from_text(description) or points_from_text(text)
    if st.points is None and (m := re.search(r"Проходная оценка:\s*" + NUM + r"\s*из\s*" + NUM, text)):
        st.points = _num(m[2])
    # Таблица попыток: строки с «Завершенные»/«Завершено» в столбце состояния
    finished = [r for r in _table_rows(html) if any(re.match(r"заверш", c, re.I) for c in r)]
    if finished:
        st.attempts_used = len(finished)
    elif st.attempts_total is not None:
        st.attempts_used = 0
    return st


def _table_rows(html: str) -> list[list[str]]:
    p = _Rows()
    p.feed(html)
    return p.rows


def parse_page(url: str, html: str, description: str | None = None) -> PortalStatus:
    if "/mod/quiz/" in url:
        return parse_quiz(html, description)
    st = parse_assign(html) if "/mod/assign/" in url else PortalStatus()
    st.points = st.points or points_from_text(description)
    return st
