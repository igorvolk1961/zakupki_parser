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


def test_publish_date_omitted_without_cutoff() -> None:
    search = _mos({"publish_date": CriteriaMapping(json_path="publishDateGreatEqual")})
    filt = _decode_filter(build_query(search, None, SearchCriteria()))
    assert "publishDateGreatEqual" not in filt


def test_keywords_into_query_param() -> None:
    search = _mos({"keywords": CriteriaMapping(query_param="searchString")})
    q = build_query(search, None, SearchCriteria(keywords=["ИТ", "нейросеть"]))
    assert "searchString=%D0%98%D0%A2%20%D0%BD%D0%B5%D0%B9%D1%80%D0%BE%D1%81%D0%B5%D1%82%D1%8C" in q


def test_empty_keywords_omitted() -> None:
    search = _mos({"keywords": CriteriaMapping(query_param="searchString")})
    q = build_query(search, None, SearchCriteria(keywords=[]))
    assert "searchString=" not in q


def test_keywords_into_name_like_json_path() -> None:
    search = _mos({"keywords": CriteriaMapping(json_path="nameLike")})
    filt = _decode_filter(build_query(search, None, SearchCriteria(keywords=["ии", "интеллект"])))
    assert filt["nameLike"] == {"value": "ии интеллект", "contains": True}


def test_deadline_from_included_by_default() -> None:
    search = SearchFilterConfig(
        query_params={},
        criteria_map={"deadline_from": CriteriaMapping(query_param="applSubmissionCloseDateFrom")},
    )
    q = build_query(search, None, SearchCriteria())
    assert "applSubmissionCloseDateFrom" in q


def test_active_only_sets_state_id_in() -> None:
    search = SearchFilterConfig(
        query_params={"filter": "{filter_json}"},
        criteria_map={"active_only": CriteriaMapping(json_path="auctionSpecificFilter.stateIdIn")},
        state_ids={"active": [19000002, 19000008]},
    )
    # все (active_only=False) — stateIdIn не ставим
    filt = _decode_filter(build_query(search, None, SearchCriteria()))
    assert "auctionSpecificFilter" not in filt
    # только активные — stateIdIn подставлен
    filt = _decode_filter(build_query(search, None, SearchCriteria(active_only=True)))
    assert filt["auctionSpecificFilter"]["stateIdIn"] == [19000002, 19000008]


def test_active_only_raw_array_flat_repeated_params() -> None:
    """active_only -> raw_array_flat: статусы повторяющимися параметрами (roseltorg status[])."""
    search = SearchFilterConfig(
        query_params={},
        criteria_map={"active_only": CriteriaMapping(raw_array_flat="status[]")},
        state_ids={"all": [5, 0, 1, 2, 3, 4], "active": [0, 1]},
    )
    q_all = build_query(search, None, SearchCriteria())
    assert "status[]=5" in q_all
    assert "status[]=0" in q_all
    assert "status[]=4" in q_all
    assert q_all.count("status[]=") == 6
    q_active = build_query(search, None, SearchCriteria(active_only=True))
    assert "status[]=0" in q_active
    assert "status[]=1" in q_active
    assert "status[]=5" not in q_active
    assert q_active.count("status[]=") == 2


def test_active_only_all_omitted_when_no_all_ids() -> None:
    """state_ids.all не задан -> при active_only=False параметр не ставится вовсе."""
    search = SearchFilterConfig(
        query_params={},
        criteria_map={"active_only": CriteriaMapping(query_param="status")},
        state_ids={"active": ["accept"]},
    )
    assert "status=" not in build_query(search, None, SearchCriteria())
    q_active = build_query(search, None, SearchCriteria(active_only=True))
    assert dict(urllib.parse.parse_qsl(q_active))["status"] == "accept"


def test_active_only_state_all_query_param() -> None:
    """active_only -> query_param + state_ids.all (b2b show=all / show=actual)."""
    search = SearchFilterConfig(
        query_params={},
        criteria_map={"active_only": CriteriaMapping(query_param="show")},
        state_ids={"all": ["all"], "active": ["actual"]},
    )
    q_all = build_query(search, None, SearchCriteria())
    assert dict(urllib.parse.parse_qsl(q_all))["show"] == "all"
    q_active = build_query(search, None, SearchCriteria(active_only=True))
    assert dict(urllib.parse.parse_qsl(q_active))["show"] == "actual"


def test_active_only_query_params_multiple() -> None:
    """active_only -> query_params (ЕИС af=on&ca=on): при active_only ставятся все."""
    search = SearchFilterConfig(
        query_params={},
        criteria_map={"active_only": CriteriaMapping(query_params={"af": "on", "ca": "on"})},
    )
    q_active = build_query(search, None, SearchCriteria(active_only=True))
    params = dict(urllib.parse.parse_qsl(q_active))
    assert params["af"] == "on"
    assert params["ca"] == "on"
    # «все» — параметры этапа не ставятся (площадка отдаёт все этапы)
    assert "af" not in build_query(search, None, SearchCriteria())
    assert "ca" not in build_query(search, None, SearchCriteria())


def test_active_only_raw_array_indexed() -> None:
    """active_only -> raw_array (etpgpb procedure[stage][N]=accepting)."""
    search = SearchFilterConfig(
        query_params={},
        criteria_map={"active_only": CriteriaMapping(raw_array="procedure[stage]")},
        state_ids={"active": ["accepting"]},
    )
    q_active = build_query(search, None, SearchCriteria(active_only=True))
    params = dict(urllib.parse.parse_qsl(q_active))
    assert params["procedure[stage][0]"] == "accepting"
    assert "procedure[stage][0]" not in build_query(search, None, SearchCriteria())


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


def test_law_toggles_query_params() -> None:
    eis = SearchFilterConfig(
        query_params={},
        criteria_map={
            "fz44": CriteriaMapping(query_param="fz44"),
            "fz223": CriteriaMapping(query_param="fz223"),
        },
    )
    both = dict(urllib.parse.parse_qsl(build_query(eis, None, SearchCriteria())))
    assert both["fz44"] == "on"
    assert both["fz223"] == "on"

    only_223 = dict(urllib.parse.parse_qsl(build_query(eis, None, SearchCriteria(fz44=False))))
    assert "fz44" not in only_223
    assert only_223["fz223"] == "on"

    none = dict(
        urllib.parse.parse_qsl(build_query(eis, None, SearchCriteria(fz44=False, fz223=False)))
    )
    assert "fz44" not in none
    assert "fz223" not in none
