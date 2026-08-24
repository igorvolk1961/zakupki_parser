"""Unit-тесты URL-фильтра fabrikant («Расширенный поиск»: ОКПД2/НМЦ/статусы).

Имена параметров сняты с SPA fabrikant (2026-08-24): массив-параметры сериализуются
в bracket-форме (`okpd2[]=`, `statuses[]=`), НМЦ — плоскими `price_from`/`price_to`.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from zakupki_parser.config.loader import _load_dom_configs
from zakupki_parser.config.models import DomConfig, PlatformDom, SearchCriteria
from zakupki_parser.parser.lister import build_list_url

REPO_ROOT = Path(__file__).resolve().parents[2]


def _fabrikant_platform() -> PlatformDom:
    data = _load_dom_configs(REPO_ROOT / "configs")
    return DomConfig.model_validate(data).platforms["fabrikant"]


def _params(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def test_nmck_maps_to_price_from_to() -> None:
    """НМЦ -> price_from/price_to (поля «Расширенный поиск»)."""
    platform = _fabrikant_platform()
    url = build_list_url(platform, None, SearchCriteria(nmck_min=100000, nmck_max=5_000_000))
    p = _params(url)
    assert p["price_from"] == "100000"
    assert p["price_to"] == "5000000"


def test_okpd2_bracket_flat_opaque_ids() -> None:
    """ОКПД2 -> okpd2[]=<opaque-id> (bracket-формат), без индексированной формы."""
    platform = _fabrikant_platform()
    url = build_list_url(platform, None, SearchCriteria(okpd_codes=["62.02", "62.03"]))
    q = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
    flat = [v for k, v in q if k == "okpd2[]"]
    assert flat == ["7h2k4Wfz9h7InEYXfDA30w", "kH-QeYrEpZDlNIpkQu9SDQ"]
    assert "okpd2[0]" not in url
    # Коды без собственного id резолвятся в id ближайшего предка (62.01.99 -> 62.01).
    url2 = build_list_url(platform, None, SearchCriteria(okpd_codes=["62.01.99"]))
    assert "okpd2[]=YgUcFLYFOTHdWmxI4nV_kg" in url2


def test_active_only_statuses_bracket() -> None:
    """active_only -> statuses[]=1&statuses[]=5 (bracket, дефолт «только активные»)."""
    platform = _fabrikant_platform()
    url = build_list_url(platform, None, SearchCriteria(active_only=True))
    q = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
    assert [v for k, v in q if k == "statuses[]"] == ["1", "5"]


def test_advanced_search_full_url() -> None:
    """«Расширенный поиск» целиком: okpd2[] + price_from/to + statuses[] + page_number."""
    platform = _fabrikant_platform()
    url = build_list_url(
        platform,
        None,
        SearchCriteria(
            okpd_codes=["62.02"],
            nmck_min=100000,
            nmck_max=5_000_000,
            active_only=True,
        ),
    )
    assert url.startswith("https://fabrikant.ru/procedure/search/purchases?")
    q = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
    params = dict(q)
    assert params["okpd2[]"] == "7h2k4Wfz9h7InEYXfDA30w"
    assert params["price_from"] == "100000"
    assert params["price_to"] == "5000000"
    assert params["page_number"] == "1"
    assert [v for k, v in q if k == "statuses[]"] == ["1", "5"]
