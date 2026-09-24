"""Unit-тесты добавления закупки «в работу» по URL (US-5.5/FR-5.5): распознавание
площадки по хосту и шаблону URL, сборка записи по детальной странице/API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from zakupki_parser.api.app.routes.procurements import (
    _match_platform_ids_by_url,
    fetch_procurement_by_url,
)
from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import (
    DomByUrlConfig,
    DomDetailConfig,
    DomListConfig,
    PlatformDom,
)
from zakupki_parser.parser.by_url import (
    ProcurementUrlError,
    fetch_record_by_url,
    resolve_platform_by_url,
)
from zakupki_parser.parser.handlers.dates import MSK, handler_regex_datetime
from zakupki_parser.parser.lister.api.parse import _etpgpb_regions

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _FakePlatform:
    url: str
    name: str = "Площадка"


PLATFORMS = {
    "zakupki_mos": _FakePlatform(url="https://zakupki.mos.ru", name="Портал поставщиков Москвы"),
    "roseltorg_44fz": _FakePlatform(url="https://www.roseltorg.ru", name="РТС-тендер 44-ФЗ"),
    "roseltorg_223fz": _FakePlatform(url="https://www.roseltorg.ru", name="РТС-тендер 223-ФЗ"),
}


def _platform(
    platform_id: str, *, api_format: str | None = None, url_pattern: str | None = None
) -> PlatformDom:
    names = {"zakupki_mos": "Портал поставщиков Москвы", "etpgpb": "ЭТП ГПБ"}
    urls = {"zakupki_mos": "https://zakupki.mos.ru", "etpgpb": "https://etpgpb.ru"}
    return PlatformDom(
        name=names[platform_id],
        url=urls[platform_id],
        list_config=DomListConfig(container="c", detail_link="a", next_page=""),
        detail=DomDetailConfig(api_format=api_format),
        by_url=DomByUrlConfig(url_pattern=url_pattern) if url_pattern else None,
    )


def test_match_platform_ids_by_url_exact_host() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "https://zakupki.mos.ru/need/123") == [
        "zakupki_mos"
    ]


def test_match_platform_ids_by_url_case_insensitive() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "HTTPS://ZAKUPKI.MOS.RU/need/123") == [
        "zakupki_mos"
    ]


def test_match_platform_ids_by_url_multiple_platforms_share_host() -> None:
    # roseltorg_44fz и roseltorg_223fz — один портал, два раздела (общий хост).
    matched = _match_platform_ids_by_url(PLATFORMS, "https://www.roseltorg.ru/tender/1")
    assert set(matched) == {"roseltorg_44fz", "roseltorg_223fz"}


def test_match_platform_ids_by_url_no_match() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "https://unknown-etp.example.com/lot/1") == []


def test_match_platform_ids_by_url_empty_or_garbage() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "") == []
    assert _match_platform_ids_by_url(PLATFORMS, "не url вовсе") == []


@pytest.mark.asyncio
async def test_fetch_procurement_by_url_not_configured_platform() -> None:
    # Площадка распознана по хосту, но подгрузка по URL для неё не настроена
    # (нет by_url) — честная ошибка (501), а не тихий частичный результат.
    class _FakeCfg:
        dom = type("D", (), {"platforms": {"zakupki_mos": _platform("zakupki_mos")}})()

    class _FakeState:
        cfg = _FakeCfg()

    with pytest.raises(NotImplementedError, match="Портал поставщиков Москвы"):
        await fetch_procurement_by_url(
            _FakeState(), ["zakupki_mos"], "https://zakupki.mos.ru/need/1"
        )


# --- Выбор площадки и номера по by_url.url_pattern (реальные конфиги) ---------


@pytest.fixture(scope="module")
def real_platforms() -> dict[str, PlatformDom]:
    return load_config(REPO_ROOT / "configs").dom.platforms


@pytest.mark.parametrize(
    ("url", "platform_id", "groups"),
    [
        (
            "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
            "?regNumber=0122200003126000002",
            "zakupki_gov_44fz",
            {"number": "0122200003126000002"},
        ),
        (
            "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html"
            "?regNumber=32616184235",
            "zakupki_gov_223fz",
            {"number": "32616184235"},
        ),
        (
            # Старый путь 223-ФЗ — так ссылается выдача поиска ЕИС.
            "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html"
            "?regNumber=32616184235",
            "zakupki_gov_223fz",
            {"number": "32616184235"},
        ),
        (
            "https://www.roseltorg.ru/procedure/0127600001726000069/1",
            "roseltorg_44fz",
            {"number": "0127600001726000069"},
        ),
        (
            "https://www.roseltorg.ru/procedure/32616404478/1",
            "roseltorg_223fz",
            {"number": "32616404478"},
        ),
        (
            "https://www.roseltorg.ru/procedure/B0308261802236",
            "roseltorg_223fz",
            {"number": "B0308261802236"},
        ),
        (
            "https://etpgpb.ru/procedures/etp/1292606-auktsion-v-elektronnoy-forme/",
            "etpgpb",
            {"kind": "etp", "platform_id": "1292606"},
        ),
    ],
)
def test_resolve_platform_by_url(
    real_platforms: dict[str, PlatformDom], url: str, platform_id: str, groups: dict[str, str]
) -> None:
    candidates = _match_platform_ids_by_url(real_platforms, url)
    resolved_id, platform, resolved_groups = resolve_platform_by_url(
        real_platforms, candidates, url
    )
    assert resolved_id == platform_id
    assert platform is real_platforms[platform_id]
    assert resolved_groups == groups


@pytest.mark.parametrize(
    "url",
    [
        # Поиск, а не карточка.
        "https://www.roseltorg.ru/procedures/search?place=44fz",
        # Вкладка «Документы» ЕИС — поля шапки и ОКПД2 с неё не собрать.
        "https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html"
        "?regNumber=0122200003126000002",
    ],
)
def test_resolve_platform_by_url_not_a_card(
    real_platforms: dict[str, PlatformDom], url: str
) -> None:
    candidates = _match_platform_ids_by_url(real_platforms, url)
    assert candidates
    with pytest.raises(ProcurementUrlError):
        resolve_platform_by_url(real_platforms, candidates, url)


# --- Сборка записи через API площадки (etpgpb) ------------------------------


class _FakeResp:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.ok = True

    async def json(self) -> Any:
        return self._payload


class _FakePage:
    def __init__(self, payload: Any) -> None:
        self.request = SimpleNamespace(get=AsyncMock(return_value=_FakeResp(payload)))


# Форма ответа /api/v2/procedures/{kind}/{platform_id}/ — по живому API
# (2026-09-24): нет kind/end_registration/company_name, regions: [null].
ETPGPB_DETAIL = {
    "data": {
        "id": "2058506",
        "type": "procedure",
        "attributes": {
            "title": "Поставка лекарственного препарата",
            "registry_number": "32616405157",
            "amount": "325785.45",
            "date_published": "2026-09-25T00:00:00.000+03:00",
            "application_reception_normal_date": "13.10.2026 09:00",
            "stage": "accepting",
            "procedure_type_name": "Аукцион на понижение (конкурентный)",
            "rebranding_truncated_path": "/procedures/etp/1292606-postavka/",
            "platform_id": 1292606,
            "region": "",
            "regions": [None],
            "lot_regions": ["Смоленская область"],
        },
        "relationships": {"company": {"data": {"id": "77", "type": "company"}}},
    },
    "included": [
        {
            "id": "77",
            "type": "company",
            "attributes": {"inn": "6731000000", "full_name": "ОГАУЗ СОМЦ"},
        },
        {
            "id": "1",
            "type": "lot",
            "attributes": {"name_status": "Прием заявок на участие"},
        },
        {
            "id": "2",
            "type": "nomenclature",
            "attributes": {"code": "21.20.10.190", "name": "Препараты"},
        },
        {
            "id": "3",
            "type": "doc",
            "attributes": {"file_name": "ТЗ.docx", "url": "https://etpgpb.ru/file/1"},
        },
    ],
}


@pytest.mark.asyncio
async def test_fetch_record_by_url_etpgpb_api(real_platforms: dict[str, PlatformDom]) -> None:
    url = "https://etpgpb.ru/procedures/etp/1292606-postavka/"
    platform = real_platforms["etpgpb"]
    page: Any = _FakePage(ETPGPB_DETAIL)

    record = await fetch_record_by_url(
        page, "etpgpb", platform, url, {"kind": "etp", "platform_id": "1292606"}
    )

    # Один запрос: поля уровня списка и детали — из одного ответа API деталей.
    assert page.request.get.await_count == 1
    assert page.request.get.await_args.args[0] == (
        "https://etpgpb.ru/api/v2/procedures/etp/1292606/"
    )
    assert record["number"] == "32616405157"
    assert record["platform_id"] == "etpgpb"
    assert record["url"] == url
    assert record["subject"] == "Поставка лекарственного препарата"
    assert record["nmck"] == 325785.45
    assert record["deadline"] == datetime(2026, 10, 13, 9, 0, tzinfo=MSK)
    assert record["law"] == "223-ФЗ"
    assert record["customer"] == "ОГАУЗ СОМЦ"
    assert record["inn"] == "6731000000"
    assert record["status"] == "Прием заявок на участие"
    assert record["is_active"] is True
    assert record["okpd2_code"] == "21.20.10.190"
    # regions: [null] не превращается в регион «None» — берутся lot_regions.
    assert record["region"] == "Смоленская область"
    assert record["files_json"] == [{"name": "ТЗ.docx", "url": "https://etpgpb.ru/file/1"}]
    # Контекст досборки деталей перед скорингом (BR-08).
    assert record["detail_api"] == {"kind": "etp", "platform_id": "1292606"}
    assert record["detail_json"]["number"] == "32616405157"


@pytest.mark.asyncio
async def test_fetch_record_by_url_without_number_is_url_error() -> None:
    platform = _platform(
        "etpgpb", api_format="etpgpb", url_pattern=r"/p/(?P<kind>\w+)/(?P<platform_id>\d+)"
    )
    payload = {"data": {"attributes": {"title": "Без номера"}}, "included": []}
    page: Any = _FakePage(payload)
    with pytest.raises(ProcurementUrlError, match="номер"):
        await fetch_record_by_url(
            page, "etpgpb", platform, "https://etpgpb.ru/p/x/0", {"kind": "x", "platform_id": "0"}
        )


# --- Обработчики, нужные подгрузке по URL -----------------------------------


def test_regex_datetime_two_digit_year() -> None:
    pattern = r"до\s+(\d{2}\.\d{2}\.\d{2,4}\s+\d{1,2}:\d{2})"
    expected = datetime(2026, 10, 2, 8, 30, tzinfo=MSK)
    assert handler_regex_datetime(" до 02.10.26 08:30 (МСК) ", pattern) == expected
    assert handler_regex_datetime("до 02.10.2026 08:30", pattern) == expected


def test_etpgpb_regions_skips_null_items() -> None:
    attrs = {"regions": [None], "lot_regions": ["Смоленская область"], "region": ""}
    assert _etpgpb_regions(attrs) == "Смоленская область"
    assert _etpgpb_regions({"regions": [None], "region": ""}) == ""
