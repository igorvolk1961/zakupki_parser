"""Unit-тесты маппинга ОКПД2 (код -> путь)."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from pathlib import Path

from zakupki_parser.config.models import SearchFilterConfig
from zakupki_parser.okpd import parse_tree_html, resolve_okpd_codes
from zakupki_parser.parser.lister import build_query

HTML = (
    '<a class="ui label" value=".1147303.1133182.">Продукты программные (62)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
    '<a class="ui label" value=".1147303.1133227.">Услуги в области ИТ (63)'
    '<i aria-hidden="true" class="delete icon"></i></a>'
    '<a class="ui label" value=".1133184.1133185.">Услуги по разработке (62.01.1)'
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


def test_build_query_with_okpd_codes(tmp_path: Path) -> None:
    tree = parse_tree_html(HTML)
    (tmp_path / "okpd.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    search = SearchFilterConfig(
        query_params={"filter": "{filter_json}"},
        filter_json={"typeIn": {"values": [2]}, "needSpecificFilter": {}},
        okpd_tree_file=str(tmp_path / "okpd.json"),
        okpd_codes=["62", "63"],
    )
    q = build_query(search, datetime(2026, 8, 4))
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
        okpd_codes=["62"],
    )
    # не должно бросать исключение
    q = build_query(search, None)
    assert q == "filter=%7B%7D"
