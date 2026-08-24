"""Unit-тесты новых механизмов движка: URL-пагинация, array-параметры, вложенные JSON-пути."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, cast

from zakupki_parser.config.models import (
    CriteriaMapping,
    SearchCriteria,
    SearchFilterConfig,
)
from zakupki_parser.parser.lister import _increment_url_page, build_query


def test_increment_url_page_adds_param() -> None:
    url = "https://x.ru/procedures/?page=1&per=20&sort=by_published_desc"
    next_url = _increment_url_page(url, "page")
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(next_url).query))
    assert params["page"] == "2"
    assert params["per"] == "20"
    assert params["sort"] == "by_published_desc"


def test_increment_url_page_from_missing_param_starts_at_1() -> None:
    url = "https://x.ru/market/?keywords=it"
    next_url = _increment_url_page(url, "page")
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(next_url).query))
    assert params["page"] == "2"
    assert params["keywords"] == "it"


def test_increment_url_page_non_numeric_falls_back_to_1() -> None:
    url = "https://x.ru/list?page=abc"
    next_url = _increment_url_page(url, "page")
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(next_url).query))
    assert params["page"] == "2"


def test_increment_url_page_preserves_raw_encoding() -> None:
    # Прочие параметры не перекодируются: %20 и %2B остаются как были.
    url = "https://x.ru/list?text=Московская%20область&a=b%2Bc&page=3"
    next_url = _increment_url_page(url, "page")
    query = urllib.parse.urlsplit(next_url).query
    assert "text=Московская%20область" in query
    assert "a=b%2Bc" in query
    assert "page=4" in query


def test_increment_url_page_without_query() -> None:
    next_url = _increment_url_page("https://x.ru/market/", "page")
    assert next_url == "https://x.ru/market/?page=2"


def test_array_query_params_preserved() -> None:
    """Вложенные array-параметры (ЭТП ГПБ: procedure[stage][0]=accepting) строятся без изменений."""
    search = SearchFilterConfig(
        query_params={
            "page": "1",
            "per": "20",
            "procedure[stage][0]": "accepting",
            "sort": "by_published_desc",
        },
        criteria_map={},
    )
    q = build_query(search, None, SearchCriteria())
    params = dict(urllib.parse.parse_qsl(q))
    assert params["procedure[stage][0]"] == "accepting"
    assert params["sort"] == "by_published_desc"


def _decode_filter(q: str) -> dict[str, Any]:
    params = dict(urllib.parse.parse_qsl(q))
    return cast(dict[str, Any], json.loads(urllib.parse.unquote(params["filter"])))


def test_nested_filter_json_paths_without_state_ids_unchanged() -> None:
    """active_only без state_ids не ломает структуру filter_json (ничего не ставим)."""
    search = SearchFilterConfig(
        query_params={"filter": "{filter_json}"},
        filter_json={"auctionSpecificFilter": {}},
        criteria_map={"active_only": CriteriaMapping(json_path="auctionSpecificFilter.stateIdIn")},
    )
    filt = _decode_filter(build_query(search, None, SearchCriteria(active_only=True)))
    assert filt["auctionSpecificFilter"] == {}
