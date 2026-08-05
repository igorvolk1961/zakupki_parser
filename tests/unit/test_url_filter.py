"""Unit-тесты URL-фильтра списка закупок (маппинг критериев из конфига)."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

from zakupki_parser.config.models import AppConfig, CriteriaMapping, SearchFilterConfig
from zakupki_parser.parser.lister import build_list_url, build_query


def _search() -> SearchFilterConfig:
    return SearchFilterConfig(
        query_params={
            "page": "1",
            "perPage": "10",
            "sortField": "relevance",
            "filter": "{filter_json}",
            "state": "{state_json}",
        },
        filter_json={
            "typeIn": {"values": [2]},
            "needSpecificFilter": {"okpdPaths": ["x.y.", "a.b."]},
        },
        state_json={"currentTab": 2},
        criteria_map={
            "publish_date": CriteriaMapping(json_path="publishDateGreatEqual"),
        },
    )


def test_build_query_maps_criteria() -> None:
    cutoff = datetime(2026, 8, 4)
    q = build_query(_search(), cutoff)
    params = dict(urllib.parse.parse_qsl(q))
    assert params["page"] == "1"
    assert params["sortField"] == "relevance"

    filt = json.loads(urllib.parse.unquote(params["filter"]))
    assert filt["typeIn"] == {"values": [2]}
    assert filt["publishDateGreatEqual"] == "04.08.2026 00:00:00"
    assert filt["needSpecificFilter"]["okpdPaths"] == ["x.y.", "a.b."]

    state = json.loads(urllib.parse.unquote(params["state"]))
    assert state == {"currentTab": 2}


def test_build_query_without_cutoff_omits_date() -> None:
    q = build_query(_search(), None)
    params = dict(urllib.parse.parse_qsl(q))
    filt = json.loads(urllib.parse.unquote(params["filter"]))
    assert "publishDateGreatEqual" not in filt


def test_build_list_url_with_search(app_config: AppConfig) -> None:
    platform = app_config.dom.platforms["zakupki_mos"]
    cutoff = datetime(2026, 8, 4)
    url = build_list_url(platform, cutoff)
    assert url.startswith("https://zakupki.mos.ru/purchase/list?")
    assert "filter=" in url
    assert "state=" in url
    assert "publishDateGreatEqual" in urllib.parse.unquote(url)


def test_build_list_url_without_search() -> None:
    from zakupki_parser.config.models import (
        DomDetailConfig,
        DomListConfig,
        PlatformDom,
    )

    platform = PlatformDom(
        name="x",
        url="https://x.ru",
        list_path="/list",
        list_config=DomListConfig(container="div.c", detail_link="a", next_page="a", variables=[]),
        detail=DomDetailConfig(variables=[], files=[]),
    )
    assert build_list_url(platform) == "https://x.ru/list"
