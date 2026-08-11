"""Unit-тесты резолва ОКПД2 для ЕИС (полное поддерево id/кодов)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from zakupki_parser.config.loader import load_config
from zakupki_parser.parser.lister import _resolve_okpd2_eis, build_list_url

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "tests" / "configs"


def _tree_file() -> str:
    cfg = load_config(CONFIGS_DIR)
    search = cfg.dom.platforms["zakupki_gov"].search
    assert search is not None and search.okpd_tree_file is not None
    return search.okpd_tree_file


def test_resolve_okpd2_eis() -> None:
    result = _resolve_okpd2_eis(["62.02"], _tree_file())
    assert result is not None
    ids = result["okpd2Ids"].split(",")
    assert ids == ["8874707"], "Собственный id выбранного кода (62.02)"
    assert "okpd2IdsCodes" not in result


def test_resolve_okpd2_eis_single_code() -> None:
    result = _resolve_okpd2_eis(["63"], _tree_file())
    assert result is not None
    assert result["okpd2Ids"] == "8873938"


def test_resolve_okpd2_eis_deep_code_uses_ancestor() -> None:
    # точного id для "62.09.20" может не быть — берётся ближайший предок
    result = _resolve_okpd2_eis(["62.09.20"], _tree_file())
    assert result is not None
    assert result["okpd2Ids"]


def test_resolve_okpd2_eis_unknown_code() -> None:
    assert _resolve_okpd2_eis(["11.1"], _tree_file()) is None


def test_files_page_url() -> None:
    from zakupki_parser.parser.detail import files_page_url

    detail = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0338"
    assert files_page_url(detail, "documents.html") == (
        "https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html?regNumber=0338"
    )
    # без query
    assert files_page_url("https://x.ru/a/view/common-info.html", "documents.html") == (
        "https://x.ru/a/view/documents.html"
    )


def test_build_list_url_includes_okpd_lists() -> None:
    cfg = load_config(CONFIGS_DIR)
    platform = cfg.dom.platforms["zakupki_gov"]
    url = build_list_url(
        platform, datetime.now() - timedelta(days=7), criteria=cfg.service.search_criteria
    )
    assert "okpd2Ids=8874707" in url
    assert "okpd2IdsWithNested=on" in url
