import pytest

from study_bot.extract.prefilter import is_candidate


@pytest.mark.parametrize(
    "text",
    [
        "До 25.10 сдать лабу 2 по ОС",
        "ДЗ по матану: номера 120-135",
        "Напоминаю, в среду контрольная по физике",
        "Курсовую нужно сдать к пятнице",
        "Зачёт по истории перенесли, срок реферата — 1 ноября",
        "Вышел тест в СДО, закрывается к понедельнику",
    ],
)
def test_task_messages_pass(text):
    assert is_candidate(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "ок",
        "Кто идёт в столовку?",
        "Скиньте фотку доски плиз, я опоздал",
    ],
)
def test_chatter_is_filtered(text):
    assert not is_candidate(text)


def test_trusted_sender_long_message_passes():
    text = "Ребята, завтра пара переносится в 405 аудиторию, принесите распечатки"
    assert not is_candidate(text, sender_id=1)
    assert is_candidate(text, sender_id=1, trusted=frozenset({1}))
