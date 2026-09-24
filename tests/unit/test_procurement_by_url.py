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
    DomByUrlRule,
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
        by_url=(
            DomByUrlConfig(rules=[DomByUrlRule(url_pattern=url_pattern)]) if url_pattern else None
        ),
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
        (
            "https://zakupki.mos.ru/need/6221685",
            "zakupki_mos",
            {"need_id": "6221685"},
        ),
        (
            "https://gz.lot-online.ru/etp_front/procedure/view/procedure/common/0372200273426000029",
            "lot_online_44",
            {"number": "0372200273426000029"},
        ),
        (
            "https://tender.lot-online.ru/procedure?procedureNumber=32616405724&lotNumber=1",
            "lot_online_223",
            {"number": "32616405724", "lot": "1"},
        ),
        (
            "https://www.b2b-center.ru/market/buldozer/tender-4614213/#btid=2",
            "b2b_center",
            {"number": "4614213"},
        ),
        (
            "https://44.fabrikant.ru/44/procedure/ea21/0321300003026000199",
            "fabrikant",
            {"number": "0321300003026000199"},
        ),
        (
            "https://fabrikant.ru/v2/trades/procedure/view/LZl6Qa0jSbv2qcFgSLRjBQ",
            "fabrikant",
            {},
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
    def __init__(self, payload: Any = None, posts: list[Any] | None = None) -> None:
        self.request = SimpleNamespace(
            get=AsyncMock(return_value=_FakeResp(payload)),
            post=AsyncMock(side_effect=[_FakeResp(p) for p in (posts or [])]),
        )


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


# Форма ответа /newapi/api/Need/Get?needId= — по живому API (2026-09-24).
MOS_DETAIL = {
    "id": 6177179,
    "name": "Активация установленных комплектов оборудования",
    "region": {"name": " Московская область"},
    "customer": {"name": 'МУП "ТЕПЛО КОЛОМНЫ"', "id": 14781607},
    "state": {"name": "Прием предложений завершен"},
    "proposalStartDate": "17.08.2026 13:56:07",
    "proposalEndDate": "19.08.2026 13:56:00",
    "nmck": 561973.33,
    "federalLawName": "223-ФЗ",
    "items": [{"okpd": {"code": "61.10.20.110", "name": "Услуги операторов связи"}}],
    "files": [{"name": "Документация.docx", "id": 281353068}],
}


@pytest.mark.asyncio
async def test_fetch_record_by_url_mos_api(real_platforms: dict[str, PlatformDom]) -> None:
    """mos.ru: один запрос Need/Get отдаёт и поля списка, и детали."""
    url = "https://zakupki.mos.ru/need/6177179"
    page: Any = _FakePage(payload=MOS_DETAIL)

    record = await fetch_record_by_url(
        page, "zakupki_mos", real_platforms["zakupki_mos"], url, {"need_id": "6177179"}
    )

    assert page.request.get.await_count == 1
    assert page.request.get.await_args.args[0] == (
        "https://zakupki.mos.ru/newapi/api/Need/Get?needId=6177179"
    )
    assert record["number"] == "6177179"
    assert record["subject"] == "Активация установленных комплектов оборудования"
    assert record["nmck"] == 561973.33
    assert record["customer"] == 'МУП "ТЕПЛО КОЛОМНЫ"'
    assert record["region"] == "Московская область"
    assert record["status"] == "Прием предложений завершен"
    assert record["law"] == "223-ФЗ"
    assert record["publication_date"] == datetime(2026, 8, 17, 13, 56, 7, tzinfo=MSK)
    assert record["deadline"] == datetime(2026, 8, 19, 13, 56, tzinfo=MSK)
    assert record["okpd2_code"] == "61.10.20.110"
    assert record["files_json"] == [
        {
            "name": "Документация.docx",
            "url": "https://zakupki.mos.ru/newapi/api/FileStorage/Download?id=281353068",
        }
    ]
    assert record["detail_api"] == {"need_id": "6177179"}


# Форма ответа /api-gateway/etp/procedure/{номер}/{лот} — по живому API (2026-09-24).
TENDER_223_DETAIL = {
    "commonInfo": {
        "eisNumber": "32616405724",
        "lotNumber": "1",
        "title": "Поставка запорной арматуры",
        "price": "891676.00",
        "stage": {"title": "Идет прием заявок", "group": "DEMANDS_STARTED"},
        "customerOkato": "Москва, г",
        "regionOkato": "Самарская, обл",
        "purchaseMethod": "Запрос котировок в электронной форме",
    },
    "organization": {"inn": "6345012488", "title": 'АО "ГИДРОРЕМОНТ-ВВК"'},
    "customers": [{"inn": "6345012488", "title": 'АО "ГИДРОРЕМОНТ-ВВК"'}],
    "productionNomenclatures": [{"okpd2Title": "28.14: Поставка запорной арматуры"}],
    "notices": [
        {
            "publishDate": "2026-09-24T11:52:54.000+03:00",
            "fileSignResponse": [{"fileDTO": {"uuid": "u1", "fileName": "Док.rar"}}],
        }
    ],
    "stages": [
        {
            "stageList": [
                {
                    "title": "Окончание подачи заявок",
                    "code": "GD_END",
                    "date": "2026-10-02",
                    "time": "12:00:00",
                },
            ]
        }
    ],
}


@pytest.mark.asyncio
async def test_fetch_record_by_url_tender_223_api(real_platforms: dict[str, PlatformDom]) -> None:
    """tender.lot-online 223-ФЗ: один запрос отдаёт поля списка и детали."""
    url = "https://tender.lot-online.ru/procedure?procedureNumber=32616405724&lotNumber=1"
    page: Any = _FakePage(payload=TENDER_223_DETAIL)

    record = await fetch_record_by_url(
        page,
        "lot_online_223",
        real_platforms["lot_online_223"],
        url,
        {"number": "32616405724", "lot": "1"},
    )

    assert page.request.get.await_args.args[0] == (
        "https://tender.lot-online.ru/api-gateway/etp/procedure/32616405724/1"
    )
    assert record["number"] == "32616405724"
    assert record["subject"] == "Поставка запорной арматуры"
    assert record["nmck"] == 891676.0
    assert record["customer"] == 'АО "ГИДРОРЕМОНТ-ВВК"'
    assert record["status"] == "Идет прием заявок"
    assert record["purchase_type"] == "Запрос котировок в электронной форме"
    assert record["region"] == "Москва, г"
    assert record["law"] == "223-ФЗ"
    assert record["publication_date"] == datetime(2026, 9, 24, 11, 52, 54, tzinfo=MSK)
    assert record["deadline"] == datetime(2026, 10, 2, 12, 0, tzinfo=MSK)
    assert record["okpd2_code"] == "28.14"
    assert record["inn"] == "6345012488"
    assert record["files_json"] == [
        {
            "name": "Док.rar",
            "url": "https://tender.lot-online.ru/etp/downloadppf?uuid=u1",
        }
    ]
    assert record["detail_api"] == {"number": "32616405724", "lot": "1"}


# Форма ответов /etp_back/api/get (sphinx CommonData + Purchase.LotInfo) — живой API.
LOT_ONLINE_SPHINX = {
    "data": {
        "entities": [
            {
                "procedure": {
                    "purchaseNumber": "0372200273426000029",
                    "purchaseObjectInfo": "Выполнение ремонтных работ",
                    "status": "accept",
                    "substatus": "Прием заявок",
                    "number": 215032,
                    "direction": "44fz",
                    "placer": {"fullName": "ГБДОУ ДЕТСКИЙ САД №1", "inn": "7806081336"},
                    "deliveryAddress": "Российская Федерация, г. Санкт-Петербург",
                    "publicationDateTime": "24.09.2026 11:58",
                    "requestEndGiveDateTime": "01.10.2026 09:00",
                }
            }
        ]
    }
}
LOT_ONLINE_LOTINFO = {
    "data": {
        "entities": [
            {
                "procedure": {
                    "lotInfo": {
                        "items": [
                            {"okpd2Code": "20.20.14.000", "okpd2Name": "Средства дезинфекционные"}
                        ],
                        "maxSum": "217 899.00",
                    }
                }
            }
        ]
    }
}


@pytest.mark.asyncio
async def test_fetch_record_by_url_lot_online_44_api(
    real_platforms: dict[str, PlatformDom],
) -> None:
    """gz lot-online 44-ФЗ: sphinx (поля списка) + Purchase.LotInfo (ОКПД2/НМЦК)."""
    url = "https://gz.lot-online.ru/etp_front/procedure/view/procedure/common/0372200273426000029"
    page: Any = _FakePage(posts=[LOT_ONLINE_SPHINX, LOT_ONLINE_LOTINFO])

    record = await fetch_record_by_url(
        page,
        "lot_online_44",
        real_platforms["lot_online_44"],
        url,
        {"number": "0372200273426000029"},
    )

    assert page.request.post.await_count == 2
    sphinx_body = page.request.post.await_args_list[0].kwargs["data"]
    assert sphinx_body["rules"] == ["Procedure.Info", "Procedure.CommonData"]
    assert sphinx_body["conditions"] == {"procedure.purchaseNumber": "0372200273426000029"}
    assert page.request.post.await_args_list[1].kwargs["data"]["conditions"] == {
        "procedure.id": 215032
    }
    assert record["number"] == "0372200273426000029"
    assert record["subject"] == "Выполнение ремонтных работ"
    assert record["customer"] == "ГБДОУ ДЕТСКИЙ САД №1"
    assert record["inn"] == "7806081336"
    assert record["status"] == "Прием заявок"
    assert record["region"] == "Российская Федерация, г. Санкт-Петербург"
    assert record["law"] == "44-ФЗ"
    assert record["publication_date"] == datetime(2026, 9, 24, 11, 58, tzinfo=MSK)
    assert record["deadline"] == datetime(2026, 10, 1, 9, 0, tzinfo=MSK)
    assert record["nmck"] == 217899.0
    assert record["okpd2_code"] == "20.20.14.000"
    # Контекст досборки деталей перед скорингом — внутренний id (BR-08).
    assert record["detail_api"] == {"id": 215032}


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


def test_regex_datetime_normalizes_nbsp_and_bullet() -> None:
    """fabrikant: «24.09.2026  •  11:56 (МСК+00:00)» — NBSP/«•» нормализуются."""
    pattern = r"(\d{2}\.\d{2}\.\d{4}[^0-9]+\d{2}:\d{2})"
    assert handler_regex_datetime("24.09.2026 \xa0•\xa011:56 (МСК+00:00)", pattern) == datetime(
        2026, 9, 24, 11, 56, tzinfo=MSK
    )


def test_etpgpb_regions_skips_null_items() -> None:
    attrs = {"regions": [None], "lot_regions": ["Смоленская область"], "region": ""}
    assert _etpgpb_regions(attrs) == "Смоленская область"
    assert _etpgpb_regions({"regions": [None], "region": ""}) == ""
