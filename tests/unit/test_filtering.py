"""Тесты клиентской пост-фильтрации закупок по ключевым словам (R9)."""

from __future__ import annotations

from zakupki_parser.parser.filtering import exclusions_present, keywords_match


def _record(subject: str) -> dict[str, str]:
    return {"number": "N-1", "subject": subject}


def test_keywords_match_plain_word() -> None:
    assert keywords_match(_record("Разработка ИИ-ассистента"), ["ИИ"])
    assert not keywords_match(_record("Ремонт помещения"), ["ИИ"])
    # Пустой список слов — фильтра нет.
    assert keywords_match(_record("Любая закупка"), [])


def test_keywords_match_stem() -> None:
    # «разработ*» ловит «разработка», «разработке», «разработку».
    assert keywords_match(_record("Разработка программного обеспечения"), ["разработ*"])
    assert keywords_match(_record("в разработке систем"), ["разработ*"])
    assert not keywords_match(_record("Поставка готового ПО"), ["разработ*"])


def test_keywords_match_multiword_phrase() -> None:
    # Все токены фразы (со стеб-префиксами) присутствуют.
    assert keywords_match(
        _record("Внедрение информационных систем на предприятии"),
        ["внедрен* информацион* систем*"],
    )
    assert not keywords_match(_record("Внедрение CRM"), ["внедрен* информацион* систем*"])


def test_keywords_match_proximity() -> None:
    # (автоматизир* систем* учет*)~2 — не более 2 слов между токенами.
    expr = "(автоматизир* систем* учет*)~2"
    assert keywords_match(_record("Автоматизированная система бухгалтерского учета"), [expr])
    assert not keywords_match(_record("Учет закупок в старой автоматизированной системе"), [expr])


def test_proximity_window_examples() -> None:
    """~N = не более N слов МЕЖДУ токенами (как в Lucene-slack)."""
    cases = [
        ("(систем* учет*)~0", "система учета", True),
        ("(систем* учет*)~0", "система коммерческого учета", False),
        ("(систем* учет*)~1", "система учета", True),
        ("(систем* учет*)~1", "система коммерческого учета", True),
        ("(систем* учет*)~1", "система автоматизированного коммерческого учета", False),
        ("(систем* учет*)~2", "система автоматизированного коммерческого учета", True),
    ]
    for expr, subject, expected in cases:
        assert keywords_match(_record(subject), [expr]) is expected, (expr, subject)


def test_keywords_match_exact_phrase() -> None:
    assert keywords_match(_record("поставка 1С Документооборот"), ["1С Документооборот"])
    assert not keywords_match(_record("поставка 1С Зарплата"), ["1С Документооборот"])


def test_exclusions_present() -> None:
    assert exclusions_present(_record("Ремонт и модернизация"), ["ремонт"])
    assert exclusions_present(_record("Ремонтные работы"), ["ремонт*"])
    assert not exclusions_present(_record("Разработка ИИ"), ["ремонт"])
    # Пустой список — ничего не исключается.
    assert not exclusions_present(_record("Любая закупка"), [])


def test_empty_subject() -> None:
    # Пустое описание: позитивные слова не совпадают (закупка отбрасывается),
    # слова-исключения — не срабатывают.
    assert not keywords_match({"number": "N", "subject": ""}, ["ИИ"])
    assert not exclusions_present({"number": "N", "subject": ""}, ["ремонт"])
