"""Юнит-тесты транслятора DSL -> tsquery (формат вывода, без БД).

Параллельная проверка результата на реальном Postgres — отдельно, в
``tests/integration/test_filtering_tsquery_parity.py`` (требует ``ZAKUPKI_TEST_DSN``).
"""

from __future__ import annotations

from zakupki_parser.parser.filtering_tsquery import (
    compile_keyword_to_tsquery,
    compile_keywords_to_tsquery,
)


def test_compile_plain_word() -> None:
    assert compile_keyword_to_tsquery("ИИ") == "'ИИ'"


def test_compile_stem_prefix() -> None:
    assert compile_keyword_to_tsquery("разработ*") == "'разработ':*"


def test_compile_multiword_phrase_is_conjunction() -> None:
    # «Фраза» без ~N — конъюнкция независимых токенов, НЕ проверка смежности.
    assert (
        compile_keyword_to_tsquery("внедрен* информацион* систем*")
        == "'внедрен':* & 'информацион':* & 'систем':*"
    )


def test_compile_exact_phrase_mixed_tokens() -> None:
    assert compile_keyword_to_tsquery("1С Документооборот") == "'1С' & 'Документооборот'"


def test_compile_proximity_two_tokens() -> None:
    assert compile_keyword_to_tsquery("(систем* учет*)~0") == "('систем':* <1> 'учет':*)"
    assert (
        compile_keyword_to_tsquery("(систем* учет*)~1")
        == "('систем':* <1> 'учет':* | 'систем':* <2> 'учет':*)"
    )
    assert (
        compile_keyword_to_tsquery("(систем* учет*)~2")
        == "('систем':* <1> 'учет':* | 'систем':* <2> 'учет':* | 'систем':* <3> 'учет':*)"
    )


def test_compile_proximity_three_tokens_chains_pairs() -> None:
    result = compile_keyword_to_tsquery("(автоматизир* систем* учет*)~1")
    assert result == (
        "('автоматизир':* <1> 'систем':* | 'автоматизир':* <2> 'систем':*)"
        " & ('систем':* <1> 'учет':* | 'систем':* <2> 'учет':*)"
    )


def test_compile_proximity_single_token_degenerates_to_lexeme() -> None:
    assert compile_keyword_to_tsquery("(систем*)~2") == "'систем':*"


def test_compile_proximity_empty_group_is_none() -> None:
    # "()~2" (без пробела) не матчит _PROXIMITY_RE (нужен хотя бы 1 символ внутри
    # скобок) и трактуется как литеральный токен — как и в Python-движке filtering.py.
    # Вырожденная группа — это "( )~2" (пробел внутри скобок -> пустой список токенов
    # после split()).
    assert compile_keyword_to_tsquery("( )~2") is None


def test_compile_keyword_empty_string_is_none() -> None:
    assert compile_keyword_to_tsquery("") is None


def test_compile_keywords_empty_list_is_none() -> None:
    assert compile_keywords_to_tsquery([]) is None


def test_compile_keywords_single_returns_bare_expression() -> None:
    assert compile_keywords_to_tsquery(["ИИ"]) == "'ИИ'"


def test_compile_keywords_multiple_are_or_combined_and_parenthesized() -> None:
    result = compile_keywords_to_tsquery(["ИИ", "внедрен* систем*"])
    assert result == "('ИИ') | ('внедрен':* & 'систем':*)"


def test_compile_keywords_skips_degenerate_expressions() -> None:
    # Вырожденное выражение (пустая проксимити-группа) не попадает в OR-объединение.
    assert compile_keywords_to_tsquery(["ИИ", "( )~1"]) == "'ИИ'"
    assert compile_keywords_to_tsquery(["( )~1"]) is None
