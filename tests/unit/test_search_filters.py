"""Unit-тесты серверных фильтров URL-поиска (criteria_map: okpd2/ключевые слова/НМЦК/регион)."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any, cast

from zakupki_parser.config.models import (
    CriteriaMapping,
    SearchCriteria,
    SearchFilterConfig,
)
from zakupki_parser.parser.lister import build_query


def _decode_filter(q: str) -> dict[str, Any]:
    params = dict(urllib.parse.parse_qsl(q))
    return cast(dict[str, Any], json.loads(urllib.parse.unquote(params["filter"])))


def _mos(criteria_map: dict[str, CriteriaMapping]) -> SearchFilterConfig:
    return SearchFilterConfig(
        query_params={"filter": "{filter_json}", "state": "{state_json}"},
        filter_json={"typeIn": {"values": [2]}, "needSpecificFilter": {}},
        state_json={"currentTab": 2},
        criteria_map=criteria_map,
    )


def test_keywords_into_filter_json() -> None:
    search = _mos({"keywords": CriteriaMapping(json_path="searchString")})
    criteria = SearchCriteria(keywords=["искусственный интеллект", "автоматизация"])
    filt = _decode_filter(build_query(search, None, criteria))
    assert filt["searchString"] == "искусственный интеллект автоматизация"


def test_keywords_empty_not_in_filter() -> None:
    search = _mos({"keywords": CriteriaMapping(json_path="searchString")})
    filt = _decode_filter(build_query(search, None, SearchCriteria()))
    assert "searchString" not in filt


def test_nmck_range_into_filter_json() -> None:
    search = _mos(
        {
            "nmck_min": CriteriaMapping(json_path="priceFrom"),
            "nmck_max": CriteriaMapping(json_path="priceTo"),
        }
    )
    criteria = SearchCriteria(nmck_min=100000, nmck_max=5000000)
    filt = _decode_filter(build_query(search, None, criteria))
    assert filt["priceFrom"] == 100000
    assert filt["priceTo"] == 5000000


def test_nmck_min_only_omits_max() -> None:
    search = _mos(
        {
            "nmck_min": CriteriaMapping(json_path="priceFrom"),
            "nmck_max": CriteriaMapping(json_path="priceTo"),
        }
    )
    filt = _decode_filter(build_query(search, None, SearchCriteria(nmck_min=100000)))
    assert filt["priceFrom"] == 100000
    assert "priceTo" not in filt


def test_okpd2_resolved_into_need_specific(tmp_path: Path) -> None:
    tree = {"code_to_path": {"77": ".77.100.", "50": ".50.200."}}
    tree_file = tmp_path / "okpd.json"
    tree_file.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    search = _mos({"okpd2": CriteriaMapping(json_path="needSpecificFilter.okpdPaths")})
    search.okpd_tree_file = str(tree_file)
    filt = _decode_filter(build_query(search, None, SearchCriteria(okpd_codes=["77", "50"])))
    assert filt["needSpecificFilter"]["okpdPaths"] == [".77.100.", ".50.200."]


def test_region_resolved_into_need_specific(tmp_path: Path) -> None:
    tree = {"code_to_path": {"77": ".77.100."}}
    tree_file = tmp_path / "region.json"
    tree_file.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    search = _mos({"region": CriteriaMapping(json_path="needSpecificFilter.regionPaths")})
    search.region_tree_file = str(tree_file)
    filt = _decode_filter(build_query(search, None, SearchCriteria(region_codes=["77"])))
    assert filt["needSpecificFilter"]["regionPaths"] == [".77.100."]


def test_region_skipped_without_mapping(tmp_path: Path) -> None:
    search = _mos({"region": CriteriaMapping(json_path="needSpecificFilter.regionPaths")})
    filt = _decode_filter(build_query(search, None, SearchCriteria(region_codes=["77"])))
    assert "regionPaths" not in filt.get("needSpecificFilter", {})


def test_publish_date_omitted_without_cutoff() -> None:
    search = _mos({"publish_date": CriteriaMapping(json_path="publishDateGreatEqual")})
    filt = _decode_filter(build_query(search, None, SearchCriteria()))
    assert "publishDateGreatEqual" not in filt


def test_query_param_criteria_flat_params() -> None:
    # ЕИС-стиль: критерий -> плоский query-параметр. Не заданный — пропускается.
    eis = SearchFilterConfig(
        query_params={"fz44": "on"},
        criteria_map={"nmck_min": CriteriaMapping(query_param="priceFrom")},
    )
    no_price = dict(urllib.parse.parse_qsl(build_query(eis, None, SearchCriteria())))
    assert no_price["fz44"] == "on"
    assert "priceFrom" not in no_price

    with_price = dict(
        urllib.parse.parse_qsl(build_query(eis, None, SearchCriteria(nmck_min=200000)))
    )
    assert with_price["priceFrom"] == "200000"
