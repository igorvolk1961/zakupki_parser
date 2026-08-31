"""Тесты серверной предфильтрации по ключевым словам (keyword_query_param)."""

from __future__ import annotations

import urllib.parse

from zakupki_parser.config.models.dom.search import SearchFilterConfig
from zakupki_parser.parser.lister import build_query, keyword_batches, keyword_search_string


def test_keyword_search_string_single_and_phrase_and_proximity() -> None:
    """Одиночные слова без скобок, многословные — в скобках, проксимити ~N — без ~N."""
    result = keyword_search_string(
        [
            "ИИ",
            "автоматизац*",
            "искусственн* интеллект*",
            "(автоматизир* систем* учет*)~2",
            "(систем* коммерческ* учет*)~2",
        ]
    )
    assert result == (
        "ИИ или автоматизац* или (искусственн* интеллект*) "
        "или (автоматизир* систем* учет*) или (систем* коммерческ* учет*)"
    )


def test_keyword_search_string_empty() -> None:
    assert keyword_search_string([]) == ""
    assert keyword_search_string(["   ", '"', "'"]) in ("", " или ")


def test_keyword_search_string_no_join_when_single() -> None:
    assert keyword_search_string(["RAG"]) == "RAG"


def test_build_query_injects_keyword_param() -> None:
    search = SearchFilterConfig(keyword_query_param="f_keyword")
    query = build_query(search, None, keywords=["ИИ", "автоматизац*"])
    params = dict(urllib.parse.parse_qsl(query))
    assert params["f_keyword"] == "ИИ или автоматизац*"


def test_build_query_omits_keyword_param_without_config() -> None:
    search = SearchFilterConfig()
    query = build_query(search, None, keywords=["ИИ"])
    assert "f_keyword" not in query
    # Без keyword_query_param ключевые слова в запрос не попадают (R9, клиентская фильтрация).
    assert "keywords" not in query


def test_build_query_skips_proximity_suffix() -> None:
    """Проксимити `(w1 w2)~N` уходит как фраза `(w1 w2)` — без `~N`."""
    search = SearchFilterConfig(keyword_query_param="f_keyword")
    query = build_query(search, None, keywords=["(автоматизир* систем* учет*)~2"])
    params = dict(urllib.parse.parse_qsl(query))
    assert "~2" not in params["f_keyword"]
    # Скобки остаются (~N убирается).
    assert params["f_keyword"] == "(автоматизир* систем* учет*)"


def test_build_query_skips_keyword_param_when_too_long() -> None:
    """Слишком длинная строка предфильтрации не подставляется (иначе сломался бы URL)."""
    search = SearchFilterConfig(keyword_query_param="f_keyword")
    long_keywords = ["very-long-keyword-token-" + "x" * 60] * 200
    query = build_query(search, None, keywords=long_keywords)
    assert "f_keyword" not in query


def test_keyword_batches_single_when_fits() -> None:
    assert keyword_batches(["ИИ", "RAG"]) == [["ИИ", "RAG"]]


def test_keyword_batches_empty() -> None:
    assert keyword_batches([]) == [[]]


def test_keyword_batches_splits_and_preserves_order() -> None:
    """При превышении лимита слова дробятся на батчи, порядок и полнота сохраняются."""
    words = [
        "искусственн* интеллект*",
        "автоматизац*",
        "чат-бот*",
        "ассистент*",
        "разработк* информацион* систем*",
        "создан* платформ*",
        "интеграц*",
    ]
    limit = 500
    batches = keyword_batches(words, max_enc_len=limit)
    assert len(batches) > 1
    # Порядок и полнота слов сохраняются между батчами.
    assert [w for batch in batches for w in batch] == words
    # Каждый батч вписывается в лимит (после URL-кодирования).
    for batch in batches:
        encoded = len(urllib.parse.quote(keyword_search_string(batch), safe=""))
        assert encoded <= limit
