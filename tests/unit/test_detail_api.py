"""Unit-тесты извлечения деталей через API (detail.api_format)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from zakupki_parser.config.models import (
    DomDetailConfig,
    DomListConfig,
    PlatformDom,
)
from zakupki_parser.parser.detail_api import fetch_api_details


class _FakeResp:
    def __init__(self, payload: Any, ok: bool = True) -> None:
        self._payload = payload
        self.ok = ok

    async def json(self) -> Any:
        return self._payload


class _FakePage:
    def __init__(self, posts: list[Any] | None = None, gets: list[Any] | None = None) -> None:
        self.request = SimpleNamespace()
        self.request.post = AsyncMock(side_effect=[_FakeResp(p) for p in (posts or [])])
        self.request.get = AsyncMock(side_effect=[_FakeResp(p) for p in (gets or [])])


def _platform(detail_format: str, url: str) -> PlatformDom:
    return PlatformDom(
        name="test",
        url=url,
        list_path="/list",
        list_config=DomListConfig(container="c", detail_link="a", next_page=""),
        detail=DomDetailConfig(api_format=detail_format),
    )


@pytest.mark.asyncio
async def test_lot_online_details_uses_registry_id() -> None:
    """Детали lot-online: id из реестра используется напрямую (sphinx-резолв не нужен)."""
    platform = _platform("lot_online", "https://gz.lot-online.ru")
    lots = {
        "data": {
            "entities": [
                {
                    "procedure": {
                        "lotInfo": {
                            "items": [{"okpd2Code": "21.20.23.110", "okpd2Name": "Реагенты"}]
                        }
                    }
                }
            ]
        }
    }
    page: Any = _FakePage(posts=[lots])
    vars_, files, inn = await fetch_api_details(
        page, platform, {"number": "0108500000426004497"}, {"id": 209724}
    )
    assert vars_["okpd2_code"] == "21.20.23.110"
    assert vars_["okpd2_name"] == "Реагенты"
    # Регион в lotInfo отсутствует (gz, 2026-09-04) — ключ не ставится.
    assert "region" not in vars_
    assert files == []
    assert inn is None
    calls = page.request.post.await_args_list
    assert len(calls) == 1
    assert calls[0].kwargs["data"]["conditions"] == {"procedure.id": 209724}


@pytest.mark.asyncio
async def test_lot_online_details_fallback_sphinx_resolve() -> None:
    """Без id из реестра — резолв номера через sphinx (два запроса)."""
    platform = _platform("lot_online", "https://gz.lot-online.ru")
    resolve = {"data": {"entities": [{"procedure": {"number": 209724, "type": "EAP20"}}]}}
    lots = {
        "data": {
            "entities": [{"procedure": {"lotInfo": {"items": [{"okpd2Code": "38.22.29.000"}]}}}]
        }
    }
    page: Any = _FakePage(posts=[resolve, lots])
    vars_, _, _ = await fetch_api_details(page, platform, {"number": "0108500000426004497"}, None)
    assert vars_["okpd2_code"] == "38.22.29.000"
    calls = page.request.post.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["data"]["conditions"] == {
        "procedure.purchaseNumber": "0108500000426004497"
    }
    assert calls[1].kwargs["data"]["conditions"] == {"procedure.id": 209724}


@pytest.mark.asyncio
async def test_etpgpb_details_maps_included() -> None:
    """Детали etpgpb: JSON:API — ОКПД2 (nomenclature), заказчик/ИНН, статус, файлы."""
    platform = _platform("etpgpb", "https://etpgpb.ru")
    payload = {
        "data": {
            "id": "2026855",
            "type": "procedure",
            "attributes": {
                "registry_number": "ГП632202",
                "stage": "accepting",
                "amount": "81 733.47",
                # Регион в attributes процедуры (проверено 2026-09-04 на живом API).
                "region": "Омская область",
                "regions": ["Омская область"],
                "lot_regions": ["Омская область"],
            },
            "relationships": {
                "company": {"data": {"id": "5621", "type": "company"}},
                "lots": {"data": [{"id": "2308569", "type": "lot"}]},
            },
        },
        "included": [
            {
                "id": "5621",
                "type": "company",
                "attributes": {"inn": "0265004219", "full_name": "АО АК ОЗНА"},
            },
            {
                "id": "2308569",
                "type": "lot",
                "attributes": {"name_status": "Прием заявок на участие", "start_price": 81733.47},
            },
            {
                "id": "10948428",
                "type": "doc",
                "attributes": {"file_name": "ТЗ.docx", "url": "https://etp.gpb.ru/file/get/1"},
            },
            {
                "id": "6484103",
                "type": "nomenclature",
                "attributes": {"code": "25.12.10.190", "name": "Двери, окна и их рамы"},
            },
        ],
    }
    page: Any = _FakePage(gets=[payload])
    vars_, files, inn = await fetch_api_details(
        page, platform, {"number": "ГП632202"}, {"kind": "etp", "platform_id": "1281042"}
    )
    assert vars_["okpd2_code"] == "25.12.10.190"
    assert vars_["okpd2_name"] == "Двери, окна и их рамы"
    assert vars_["customer"] == "АО АК ОЗНА"
    assert vars_["status"] == "Прием заявок на участие"
    assert vars_["nmck"] == 81733.47
    assert vars_["region"] == "Омская область"
    assert inn == "0265004219"
    assert files == [{"name": "ТЗ.docx", "url": "https://etp.gpb.ru/file/get/1"}]

    url = page.request.get.await_args_list[0].args[0]
    assert url == "https://etpgpb.ru/api/v2/procedures/etp/1281042/"


@pytest.mark.asyncio
async def test_mos_details_maps_items_and_files() -> None:
    """Детали mos.ru: ОКПД2 из items, файлы через FileStorage/Download."""
    platform = _platform("mos", "https://zakupki.mos.ru")
    payload = {
        "customer": {"name": "МУП ТЕПЛО КОЛОМНЫ"},
        "state": {"name": "Прием предложений"},
        "nmck": 561973.33,
        "items": [{"okpd": {"code": "61.10.20.110", "name": "Услуги операторов связи"}}],
        "files": [{"name": "Документация.docx", "id": 281353068}],
    }
    page: Any = _FakePage(gets=[payload])
    vars_, files, inn = await fetch_api_details(
        page, platform, {"number": "6177179"}, {"need_id": 6177179}
    )
    assert vars_["okpd2_code"] == "61.10.20.110"
    assert vars_["okpd2_name"] == "Услуги операторов связи"
    assert vars_["customer"] == "МУП ТЕПЛО КОЛОМНЫ"
    assert vars_["status"] == "Прием предложений"
    assert vars_["nmck"] == 561973.33
    assert inn is None
    assert files == [
        {
            "name": "Документация.docx",
            "url": "https://zakupki.mos.ru/newapi/api/FileStorage/Download?id=281353068",
        }
    ]

    url = page.request.get.await_args_list[0].args[0]
    assert url == "https://zakupki.mos.ru/newapi/api/Need/Get?needId=6177179"


@pytest.mark.asyncio
async def test_tender_223_details_maps_response() -> None:
    """Детали tender.lot-online (223-ФЗ): REST /api-gateway/etp/procedure/{номер}/{лот}."""
    platform = _platform("tender_223", "https://tender.lot-online.ru")
    payload = {
        "commonInfo": {
            "eisNumber": "32616319584",
            "lotNumber": "1",
            "title": "ПИР и СМР",
            "price": 643260.86,
            "stage": {"title": "Идет прием заявок", "group": "DEMANDS_STARTED"},
            # Регион в commonInfo (проверено 2026-09-04 на живом API).
            "customerOkato": "Москва, г",
        },
        "organization": {"inn": "5260200603", "title": "ПАО РОССЕТИ ЦЕНТР"},
        "customers": [{"inn": "5260200603", "title": "ПАО РОССЕТИ ЦЕНТР И ПРИВОЛЖЬЕ"}],
        "productionNomenclatures": [
            {
                "okpd2Title": "42.22.22.110: Работы строительные по прокладке местных "
                "линий электропередачи"
            },
            {"okpd2Title": "42.22.22.110: Работы строительные по прокладке местных линий"},
        ],
        "notices": [
            {
                "name": "Извещение о проведении закупки",
                "fileSignResponse": [
                    {
                        "fileDTO": {
                            "uuid": "dbfile19718ug0000q0dupncr1mj10lc",
                            "fileName": "Документация.rar",
                            "size": 3158858,
                        }
                    }
                ],
            }
        ],
    }
    page: Any = _FakePage(gets=[payload])
    vars_, files, inn = await fetch_api_details(
        page, platform, {"number": "32616319584"}, {"number": "32616319584", "lot": "1"}
    )
    assert vars_["okpd2_code"] == "42.22.22.110"
    assert vars_["okpd2_name"] == (
        "Работы строительные по прокладке местных линий электропередачи | "
        "Работы строительные по прокладке местных линий"
    )
    assert vars_["customer"] == "ПАО РОССЕТИ ЦЕНТР И ПРИВОЛЖЬЕ"
    assert vars_["status"] == "Идет прием заявок"
    assert vars_["nmck"] == 643260.86
    assert vars_["region"] == "Москва, г"
    assert inn == "5260200603"
    assert files == [
        {
            "name": "Документация.rar",
            "url": "https://tender.lot-online.ru/etp/downloadppf?uuid=dbfile19718ug0000q0dupncr1mj10lc",
        }
    ]

    url = page.request.get.await_args_list[0].args[0]
    assert url == "https://tender.lot-online.ru/api-gateway/etp/procedure/32616319584/1"


@pytest.mark.asyncio
async def test_tender_223_details_no_number_returns_empty() -> None:
    """Детали tender_223: без номера/лота в _api — пустой результат, без запроса."""
    platform = _platform("tender_223", "https://tender.lot-online.ru")
    page: Any = _FakePage(gets=[])
    vars_, files, inn = await fetch_api_details(page, platform, {"number": "X"}, {})
    assert vars_ == {}
    assert files == []
    assert inn is None
    page.request.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_details_api_and_dom_same_interface() -> None:
    """extract_details — единый интерфейс: API- и DOM-площадки вызываются одинаково."""
    from zakupki_parser.parser.detail import extract_details

    # API-площадка: детали через JSON (fetch_api_details).
    api_platform = _platform("mos", "https://zakupki.mos.ru")
    api_page: Any = _FakePage(
        gets=[
            {
                "items": [{"okpd": {"code": "61.10.20.110", "name": "Услуги связи"}}],
                "files": [{"name": "Документация.docx", "id": 281353068}],
            }
        ]
    )
    vars_, files, inn = await extract_details(
        api_page, api_platform, {"number": "6177179"}, None, {"need_id": 6177179}
    )
    assert vars_["okpd2_code"] == "61.10.20.110"
    assert files[0]["name"] == "Документация.docx"
    assert inn is None

    # DOM-площадка (api_format пуст): детальная страница открывается.
    dom_platform = _platform("", "https://dom.example.ru")
    # Для DOM-ветки мокаем функции извлечения со страницы.
    with (
        patch("zakupki_parser.parser.detail.open_detail", new=AsyncMock()),
        patch(
            "zakupki_parser.parser.detail.extract_detail_vars",
            return_value={"subject": "Полный предмет", "nmck": 123.0},
        ),
        patch("zakupki_parser.parser.detail.capture_customer_link", return_value=None),
        patch("zakupki_parser.parser.detail.detail_files", return_value=[]),
        patch("zakupki_parser.parser.detail.resolve_inn", return_value=None),
    ):
        dom_page: Any = _FakePage(gets=[])
        vars_, files, inn = await extract_details(
            dom_page, dom_platform, {"number": "D1"}, "/proc/D1", None
        )
    assert vars_["subject"] == "Полный предмет"
    assert vars_["nmck"] == 123.0
    assert files == []
    assert inn is None
