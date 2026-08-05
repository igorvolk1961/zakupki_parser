"""Unit-тесты маппинга ОКПД2 (код -> путь)."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from pathlib import Path

from zakupki_parser.config.models import (
    CriteriaMapping,
    SearchCriteria,
    SearchFilterConfig,
)
from zakupki_parser.okpd import parse_tree_html, resolve_okpd_codes
from zakupki_parser.parser.lister import build_query

HTML = (
    '<a class="ui label" value=".1147303.1133182.">Продукты программные (62)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
    '<a class="ui label" value=".1147303.1133227.">Услуги в области ИТ (63)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
    '<a class="ui label" value=".1133184.1133185.">Услуги по разработке (62.01.1)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
    '<a class="ui label" value=".1133195.1133196.">Консультации по оборудованию (62.02.1)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
    '<a class="ui label" value=".1133195.1133199.">Консультации по ПО (62.02.2)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
    '<a class="ui label" value=".1133195.1133206.">Техподдержка ИТ (62.02.3)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
)


def test_parse_tree_html() -> None:
    tree = parse_tree_html(HTML)
    assert tree["code_to_path"]["62"] == ".1147303.1133182."
    assert tree["code_to_path"]["63"] == ".1147303.1133227."
    assert tree["code_to_path"]["62.01.1"] == ".1133184.1133185."
    assert tree["path_to_name"][".1147303.1133182."] == "Продукты программные"


def test_resolve_codes() -> None:
    tree = parse_tree_html(HTML)
    assert resolve_okpd_codes(["62", "63"], tree) == [
        ".1147303.1133182.",
        ".1147303.1133227.",
    ]
    # отсутствующий код пропускается с предупреждением, но не ломает резолв
    assert resolve_okpd_codes(["62", "99.99"], tree, warn_missing=False) == [".1147303.1133182."]


def test_resolve_any_depth_via_ancestor() -> None:
    tree = parse_tree_html(HTML)
    # точного кода "62.09" нет — берём ближайшего предка 62
    assert resolve_okpd_codes(["62.09"], tree, warn_missing=False) == [".1147303.1133182."]
    # "62.09.2.100" (глубокая вложенность) — предок 62.09.2 нет, но 62 есть
    assert resolve_okpd_codes(["62.09.2.100"], tree, warn_missing=False) == [".1147303.1133182."]
    # код 62.01.1 есть точно
    assert resolve_okpd_codes(["62.01.1"], tree, warn_missing=False) == [".1133184.1133185."]


def test_resolve_via_union_of_descendants() -> None:
    tree = parse_tree_html(HTML)
    # "62.02" нет в маппинге, но есть потомки 62.02.1/62.02.2/62.02.3
    # -> объединяем пути всех потомков (точнее, чем предок 62)
    assert resolve_okpd_codes(["62.02"], tree, warn_missing=False) == [
        ".1133195.1133196.",
        ".1133195.1133199.",
        ".1133195.1133206.",
    ]
    # глубокий код 62.02.11.000: потомков нет, ближайший предок — 62.02.1
    assert resolve_okpd_codes(["62.02.11.000"], tree, warn_missing=False) == [".1133195.1133196."]


def test_resolve_normalizes_input() -> None:
    tree = parse_tree_html(HTML)
    # разные форматы записи одного кода
    for variant in ["62", "6200", "62-00", "6 2 0 0", "62.00"]:
        assert resolve_okpd_codes([variant], tree, warn_missing=False) == [".1147303.1133182."]


def test_resolve_deduplicates() -> None:
    tree = parse_tree_html(HTML)
    # "62" и "62.99" оба резолвятся в путь 62 -> дедупликация
    assert resolve_okpd_codes(["62", "62.99"], tree, warn_missing=False) == [".1147303.1133182."]


def test_resolve_no_ancestor_skipped() -> None:
    tree = parse_tree_html(HTML)
    assert resolve_okpd_codes(["11.1"], tree, warn_missing=False) == []


def test_build_query_with_okpd_codes(tmp_path: Path) -> None:
    tree = parse_tree_html(HTML)
    (tmp_path / "okpd.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    search = SearchFilterConfig(
        query_params={"filter": "{filter_json}"},
        filter_json={"typeIn": {"values": [2]}, "needSpecificFilter": {}},
        okpd_tree_file=str(tmp_path / "okpd.json"),
        criteria_map={"okpd2": CriteriaMapping(json_path="needSpecificFilter.okpdPaths")},
    )
    q = build_query(search, datetime(2026, 8, 4), SearchCriteria(okpd_codes=["62", "63"]))
    params = dict(urllib.parse.parse_qsl(q))
    filt = json.loads(urllib.parse.unquote(params["filter"]))
    assert filt["needSpecificFilter"]["okpdPaths"] == [
        ".1147303.1133182.",
        ".1147303.1133227.",
    ]


def test_build_query_without_okpd_codes(tmp_path: Path) -> None:
    search = SearchFilterConfig(
        query_params={"filter": "{filter_json}"},
        filter_json={"typeIn": {"values": [2]}},
    )
    q = build_query(search, None)
    filt = json.loads(urllib.parse.unquote(dict(urllib.parse.parse_qsl(q))["filter"]))
    assert "needSpecificFilter" not in filt


def test_missing_tree_warns(tmp_path: Path) -> None:
    search = SearchFilterConfig(
        query_params={"filter": "{filter_json}"},
        filter_json={},
        okpd_tree_file=str(tmp_path / "нет_файла.json"),
        criteria_map={"okpd2": CriteriaMapping(json_path="needSpecificFilter.okpdPaths")},
    )
    # не должно бросать исключение
    q = build_query(search, None, SearchCriteria(okpd_codes=["62"]))
    assert q == "filter=%7B%7D"
