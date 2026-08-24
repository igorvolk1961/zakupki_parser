"""Unit-тесты API-листера: построение запроса, парсинг item, обход _crawl_api."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from playwright.async_api import Page

from zakupki_parser.config.models import (
    AppConfig,
    CriteriaMapping,
    DomDetailConfig,
    DomListConfig,
    FilterMapping,
    PlatformDom,
    RetryConfig,
    SearchCriteria,
    SearchFilterConfig,
)
from zakupki_parser.parser.lister.api import build_api_list_url, parse_api_item
from zakupki_parser.parser.orchestrator import Orchestrator


def _make_api_platform(page_size: int = 2) -> PlatformDom:
    return PlatformDom(
        name="etpgpb",
        url="https://etpgpb.ru",
        list_path="/procedures/",
        list_config=DomListConfig(
            container="[data-testid='procedure-card']",
            detail_link="a",
            next_page="",
            page_param="page",
            page_size=page_size,
        ),
        detail=DomDetailConfig(),
        search=SearchFilterConfig(
            enabled=True,
            api_endpoint="/api/v2/procedures/",
            query_params={"per": "2", "sort": "by_published_desc"},
            criteria_map={
                "okpd2": CriteriaMapping(query_param="procedure[okpd]"),
                "active_only": CriteriaMapping(raw_array="procedure[stage]"),
            },
            state_ids={"active": ["accepting"]},
        ),
    )


def _params(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _make_lot_online_platform(page_size: int = 2) -> PlatformDom:
    """Площадка lot_online_44 с API-реестром (/etp_back/procedure/list)."""
    return PlatformDom(
        name="lot-online РАД (44-ФЗ)",
        url="https://gz.lot-online.ru",
        list_path="/etp_front/procedure/list",
        list_config=DomListConfig(
            container="app-procedure-card",
            detail_link="a",
            next_page="",
            page_param="offset",
            page_size=page_size,
        ),
        detail=DomDetailConfig(),
        search=SearchFilterConfig(
            enabled=True,
            api_endpoint="/etp_back/procedure/list",
            api_items_key="items",
            api_item_format="lot_online",
            api_offset_step=page_size,
            query_params={
                "limit": "2",
                "offset": "0",
                "sort[0][property]": "finishedPurchase",
                "sort[0][direction]": "ASC",
                "sort[1][property]": "publicationDateTime",
                "sort[1][direction]": "DESC",
            },
            criteria_map={
                "okpd2": CriteriaMapping(
                    filter=FilterMapping(
                        key="okpd2", property="okpd2", condition="match", value_prefix="_"
                    )
                ),
                "active_only": CriteriaMapping(
                    filter=FilterMapping(key="status", property="status", condition="in")
                ),
            },
            state_ids={"active": ["accept", "commission", "contract"]},
        ),
    )


def _lot_online_item(number: str, title: str, published: str) -> dict[str, Any]:
    return {
        "purchaseNumber": number,
        "purchaseObjectInfo": title,
        "maxSum": "0.0",
        "placerFullName": "ООО Тест",
        "status": "accept",
        "substatus": "Прием заявок",
        "typeName": "",
        "direction": "44fz",
        "publicationDateTime": published,
        "requestEndGiveDateTime": "25.08.2026 12:00",
    }


def test_lot_online_api_url_okpd_and_status() -> None:
    """ОКПД2 -> filter[okpd2][value]=_<код>, статусы -> filter[status][value][N]."""
    platform = _make_lot_online_platform()
    url = build_api_list_url(platform, SearchCriteria(okpd_codes=["62.02"], active_only=True))
    assert url.startswith("https://gz.lot-online.ru/etp_back/procedure/list?")
    p = _params(url)
    assert p["filter[okpd2][condition]"] == "match"
    assert p["filter[okpd2][value]"] == "_62.02"
    assert p["filter[status][condition]"] == "in"
    assert p["filter[status][property]"] == "status"
    assert p["filter[status][value][0]"] == "accept"
    assert p["filter[status][value][1]"] == "commission"
    assert p["filter[status][value][2]"] == "contract"
    assert p["sort[0][property]"] == "finishedPurchase"
    assert p["limit"] == "2"
    assert p["offset"] == "0"


def test_lot_online_api_url_okpd2_prefix() -> None:
    """ОКПД2 -> filter[okpd2][value]=_<код> (sphinx-префикс)."""
    platform = _make_lot_online_platform()
    url = build_api_list_url(platform, SearchCriteria(okpd_codes=["62.02"]))
    p = _params(url)
    assert p["filter[okpd2][condition]"] == "match"
    assert p["filter[okpd2][value]"] == "_62.02"


def test_lot_online_api_url_okpd2_multiple_indexed() -> None:
    """Несколько кодов ОКПД2 -> индексированные filter[okpd2][value][N]."""
    platform = _make_lot_online_platform()
    url = build_api_list_url(platform, SearchCriteria(okpd_codes=["62.02", "62.01"]))
    p = _params(url)
    assert p["filter[okpd2][value][0]"] == "_62.02"
    assert p["filter[okpd2][value][1]"] == "_62.01"


def test_parse_api_item_lot_online() -> None:
    item = _lot_online_item("0108500000426004497", "Поставка реагентов", "18.08.2026 21:14")
    item["maxSum"] = "75 618.79"
    v = parse_api_item(item, "lot_online")
    assert v["number"] == "0108500000426004497"
    assert v["subject"] == "Поставка реагентов"
    assert v["nmck"] == 75618.79
    assert v["customer"] == "ООО Тест"
    assert v["status"] == "Прием заявок"
    assert v["law"] == "44-ФЗ"
    assert v["purchase_type"] == ""
    assert v["publication_date"] is not None and v["publication_date"].tzinfo is not None
    assert v["deadline"] is not None and v["deadline"].tzinfo is not None
    assert v["detail_path"] == "/etp_front/procedure/view/procedure/common/0108500000426004497"


def test_parse_api_item_lot_online_direction_law() -> None:
    """Закон определяется по direction реестра."""
    item = _lot_online_item("32211517818.lot1", "ТЗ", "18.08.2026 21:14")
    item["direction"] = "tender223_market"
    assert parse_api_item(item, "lot_online")["law"] == "223-ФЗ"
    item["direction"] = "615pprf"
    assert parse_api_item(item, "lot_online")["law"] == "44-ФЗ"


def test_increment_url_page_with_step() -> None:
    """offset-пагинация API: offset растёт на шаг (page_size)."""
    url = "https://gz.lot-online.ru/etp_back/procedure/list?limit=10&offset=0&sort=x"
    from zakupki_parser.parser.lister import _increment_url_page

    next_url = _increment_url_page(url, "offset", step=10)
    assert "offset=10" in next_url
    assert "limit=10" in next_url
    next_url2 = _increment_url_page(next_url, "offset", step=10)
    assert "offset=20" in next_url2


def test_api_list_url_okpd_flat_without_keywords() -> None:
    """ОКПД2 — плоский параметр procedure[okpd]=код[,код]; sort остаётся по дате."""
    platform = _make_api_platform()
    url = build_api_list_url(
        platform,
        SearchCriteria(okpd_codes=["62.02", "62.01"], active_only=True),
    )
    assert url.startswith("https://etpgpb.ru/api/v2/procedures/?")
    p = _params(url)
    assert p["procedure[okpd]"] == "62.02,62.01"
    assert p["procedure[stage][0]"] == "accepting"
    assert p["sort"] == "by_published_desc"
    assert "search" not in p


def test_parse_api_item_maps_attributes() -> None:
    item: dict[str, Any] = {
        "id": "123",
        "type": "procedure",
        "attributes": {
            "registry_number": "ГП632202",
            "title": "Двери металлические",
            "amount": "1 234 567,89",
            "date_published": "2026-08-17T16:48:00.000+03:00",
            "date_last_update": None,
            "end_registration": "2026-08-21T08:00:00.000+03:00",
            "company_name": "ООО Тест",
            "stage": "accepting",
            "kind": "fz44",
            "custom_procedure_type_name": "Запрос котировок",
            "rebranding_truncated_path": "/procedures/etp/123-dveri/",
        },
    }
    v = parse_api_item(item)
    assert v["number"] == "ГП632202"
    assert v["subject"] == "Двери металлические"
    assert v["nmck"] == 1234567.89
    assert v["law"] == "44-ФЗ"
    assert v["purchase_type"] == "Запрос котировок"
    assert v["detail_path"] == "/procedures/etp/123-dveri/"
    assert v["publication_date"] is not None and v["publication_date"].tzinfo is not None
    assert v["deadline"] is not None and v["deadline"].tzinfo is not None
    assert v["status"] == "accepting"


def _item(number: str, title: str, published: str) -> dict[str, Any]:
    return {
        "id": number,
        "type": "procedure",
        "attributes": {
            "registry_number": number,
            "title": title,
            "amount": "0.0",
            "date_published": published,
            "end_registration": "2026-08-21T08:00:00.000+03:00",
            "company_name": "ООО Тест",
            "stage": "accepting",
            "kind": "fz223",
            "custom_procedure_type_name": "Запрос котировок",
            "rebranding_truncated_path": f"/procedures/etp/{number}-{title}/",
        },
    }


class _OkCircuit:
    def allow_request(self) -> bool:
        return True

    def record_success(self) -> None:
        pass

    def record_failure(self) -> None:
        pass


class _FakeDelayer:
    async def sleep(self) -> None:
        pass


class _Recorder(Orchestrator):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.processed: list[dict[str, Any]] = []

    async def _process_list_record(
        self,
        page: Page,
        list_vars: dict[str, Any],
        detail_url: str | None,
        number: Any,
        api_fields: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, bool]:
        self.processed.append({"vars": list_vars, "url": detail_url, "number": number})
        return False, number, True


class _CrawlRecorder(Orchestrator):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.crawled: list[SearchCriteria] = []

    async def _crawl(
        self,
        page: Page,
        cutoff: datetime | None,
        criteria: SearchCriteria,
        by_relevance: bool,
        retry_cfg: RetryConfig,
    ) -> None:
        self.crawled.append(criteria)


def _make_recorder(app_config: AppConfig, platform: PlatformDom) -> _Recorder:
    cfg = app_config.model_copy(deep=True)
    return _Recorder(
        cfg=cfg,
        platform_id="etpgpb",
        platform=platform,
        delayer=_FakeDelayer(),
        repository=None,
        notifier=None,
        site_cb=_OkCircuit(),
        db_cb=_OkCircuit(),
        now=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )


def _make_lot_online_recorder(app_config: AppConfig, platform: PlatformDom) -> _Recorder:
    cfg = app_config.model_copy(deep=True)
    return _Recorder(
        cfg=cfg,
        platform_id="lot_online_44",
        platform=platform,
        delayer=_FakeDelayer(),
        repository=None,
        notifier=None,
        site_cb=_OkCircuit(),
        db_cb=_OkCircuit(),
        now=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_crawl_api_processes_all_pages_and_stops_on_short_page(app_config: AppConfig) -> None:
    """Обход читает все страницы API; неполная страница — конец пагинации."""
    platform = _make_api_platform(page_size=2)
    recorder = _make_recorder(app_config, platform)
    pages = [
        [
            _item("A", "Закупка А", "2026-08-18T10:00:00.000+03:00"),
            _item("B", "Закупка Б", "2026-08-17T10:00:00.000+03:00"),
        ],
        [_item("C", "Закупка В", "2026-08-16T10:00:00.000+03:00")],
    ]
    with patch(
        "zakupki_parser.parser.orchestrator.orchestrator.fetch_api_items",
        side_effect=pages,
    ):
        await recorder._crawl_api(  # noqa: SLF001
            page=object(),  # type: ignore[arg-type]
            cutoff=datetime(2020, 1, 1, tzinfo=UTC),
            criteria=SearchCriteria(okpd_codes=["62.02"]),
            retry_cfg=RetryConfig(),
        )
    assert len(recorder.processed) == 3
    assert [r["number"] for r in recorder.processed] == ["A", "B", "C"]
    assert all(r["url"].startswith("https://etpgpb.ru/procedures/etp/") for r in recorder.processed)


@pytest.mark.asyncio
async def test_crawl_api_cutoff_stops_okpd_crawl(app_config: AppConfig) -> None:
    """Обход по ОКПД2 (дата-сортировка) останавливается на записи старше порога."""
    platform = _make_api_platform(page_size=5)
    recorder = _make_recorder(app_config, platform)
    recent = _item("NEW", "Новая", "2026-08-17T10:00:00.000+03:00")
    old = _item("OLD", "Старая", "2020-01-01T10:00:00.000+03:00")
    with patch(
        "zakupki_parser.parser.orchestrator.orchestrator.fetch_api_items",
        return_value=[recent, old],
    ):
        await recorder._crawl_api(  # noqa: SLF001
            page=object(),  # type: ignore[arg-type]
            cutoff=datetime(2026, 8, 16, tzinfo=UTC),
            criteria=SearchCriteria(okpd_codes=["62.02"]),
            retry_cfg=RetryConfig(),
        )
    assert [r["number"] for r in recorder.processed] == ["NEW"]


@pytest.mark.asyncio
async def test_crawl_api_lot_online_processes_items(app_config: AppConfig) -> None:
    """API-реестр lot-online (data.items): записи маппятся, детали — common-URL."""
    platform = _make_lot_online_platform(page_size=2)
    recorder = _make_lot_online_recorder(app_config, platform)
    with patch(
        "zakupki_parser.parser.orchestrator.orchestrator.fetch_api_items",
        side_effect=[
            [
                _lot_online_item("0108500000426004497", "Реагенты", "18.08.2026 21:14"),
                _lot_online_item("0372200023026000231", "Картриджи", "17.08.2026 21:14"),
            ],
            [],
        ],
    ):
        await recorder._crawl_api(  # noqa: SLF001
            page=object(),  # type: ignore[arg-type]
            cutoff=datetime(2020, 1, 1, tzinfo=UTC),
            criteria=SearchCriteria(okpd_codes=["62.02"]),
            retry_cfg=RetryConfig(),
        )
    assert [r["number"] for r in recorder.processed] == [
        "0108500000426004497",
        "0372200023026000231",
    ]
    assert all(
        r["url"]
        == f"https://gz.lot-online.ru/etp_front/procedure/view/procedure/common/{r['number']}"
        for r in recorder.processed
    )


@pytest.mark.asyncio
async def test_crawl_api_skips_known_procurements(app_config: AppConfig) -> None:
    """API-обход не открывает детали уже сохранённых закупок (как DOM-обход)."""
    platform = _make_api_platform(page_size=5)
    recorder = _make_recorder(app_config, platform)
    recorder._known_numbers = {"B"}  # noqa: SLF001
    items = [
        _item("A", "Новая", "2026-08-18T10:00:00.000+03:00"),
        _item("B", "Известная", "2026-08-17T10:00:00.000+03:00"),
    ]
    with patch(
        "zakupki_parser.parser.orchestrator.orchestrator.fetch_api_items",
        return_value=items,
    ):
        await recorder._crawl_api(  # noqa: SLF001
            page=object(),  # type: ignore[arg-type]
            cutoff=datetime(2020, 1, 1, tzinfo=UTC),
            criteria=SearchCriteria(okpd_codes=["62.02"]),
            retry_cfg=RetryConfig(),
        )
    assert [r["number"] for r in recorder.processed] == ["A"]


@pytest.mark.asyncio
async def test_run_crawls_by_okpd_only(app_config: AppConfig) -> None:
    """R9: слова не передаются на площадку — серверный обход только по кодам ОКПД2."""
    platform = _make_api_platform()
    cfg = app_config.model_copy(deep=True)
    cfg.service.search_criteria.okpd_codes = ["62.02"]
    recorder = _CrawlRecorder(
        cfg=cfg,
        platform_id="etpgpb",
        platform=platform,
        delayer=_FakeDelayer(),
        repository=None,
        notifier=None,
        site_cb=_OkCircuit(),
        db_cb=_OkCircuit(),
        now=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )
    await recorder.run(page=object())  # type: ignore[arg-type]

    assert [c.okpd_codes for c in recorder.crawled] == [["62.02"]]


def _make_mos_platform(page_size: int = 2) -> PlatformDom:
    """Площадка zakupki_mos с API-реестром (Query API, take/skip)."""
    return PlatformDom(
        name="Портал поставщиков Москвы",
        url="https://zakupki.mos.ru",
        list_path="/purchase/list",
        list_config=DomListConfig(
            container="div",
            detail_link="a",
            next_page="",
            page_param="page",
            page_size=page_size,
        ),
        detail=DomDetailConfig(),
        search=SearchFilterConfig(
            enabled=True,
            api_endpoint="https://old.zakupki.mos.ru/api/Cssp/Purchase/Query",
            api_items_key="items",
            api_item_format="mos",
            api_offset_param="skip",
            api_offset_step=page_size,
            query_params={
                "queryDto": (
                    '{"filter":{filter_json},"order":[{"field":"publishDate","desc":true}],'
                    f'"withCount":true,"take":"{page_size}","skip":{{skip}}}}'
                )
            },
            filter_json={"typeIn": {"values": [2]}, "needSpecificFilter": {}},
            criteria_map={
                "active_only": CriteriaMapping(json_path="needSpecificFilter.stateIdIn"),
            },
            state_ids={"active": [20000002]},
        ),
    )


def _mos_item(number: str, title: str, begin: str) -> dict[str, Any]:
    return {
        "needId": int(number),
        "number": number,
        "name": title,
        "startPrice": 100.0,
        "stateName": "Прием предложений",
        "stateId": 20000002,
        "beginDate": begin,
        "endDate": "19.08.2026 13:56:00",
        "federalLawName": "223-ФЗ",
        "tenderTypeName": "Закупка малого объема",
        "customers": [{"name": "ООО Тест", "inn": "5022030985"}],
    }


def test_parse_api_item_mos() -> None:
    """Item реестра mos.ru: needId, заказчик с ИНН прямо в карточке."""
    v = parse_api_item(_mos_item("6177179", "Активация оборудования", "17.08.2026 13:56:07"), "mos")
    assert v["number"] == "6177179"
    assert v["subject"] == "Активация оборудования"
    assert v["nmck"] == 100.0
    assert v["customer"] == "ООО Тест"
    assert v["inn"] == "5022030985"
    assert v["status"] == "Прием предложений"
    assert v["law"] == "223-ФЗ"
    assert v["purchase_type"] == "Закупка малого объема"
    assert v["publication_date"] is not None and v["publication_date"].tzinfo is not None
    assert v["detail_path"] == "/need/6177179"
    assert v["_api"] == {"need_id": 6177179}


def test_parse_api_item_lot_online_carries_internal_id() -> None:
    """Item реестра lot-online: внутренний id (number) для деталей через API."""
    item = _lot_online_item("0108500000426004497", "Реагенты", "18.08.2026 21:14")
    item["number"] = 209724
    v = parse_api_item(item, "lot_online")
    assert v["_api"] == {"id": 209724}


def test_parse_api_item_tender_223() -> None:
    """Item реестра tender.lot-online: даты из частей, детали — DOM-URL по номеру."""
    item = {
        "uuid": "abc123",
        "eisNumber": "32616302720",
        "etpNumber": "RAD260040615",
        "lotNumber": 1,
        "title": "Поставка сервера",
        "price": 2700000.00,
        "organizationTitle": "ПАО РОСТЕЛЕКОМ",
        "status": "Идет прием заявок",
        "purchaseMethod": "Открытый запрос цен",
        "publicationDate": {"date": "2026-08-18", "time": "20:33:05", "timezone": "MCK"},
        "demandEndDate": {"date": "2026-08-24", "time": "09:00:00", "timezone": "MCK"},
    }
    v = parse_api_item(item, "tender_223")
    assert v["number"] == "32616302720"
    assert v["subject"] == "Поставка сервера"
    assert v["nmck"] == 2700000.0
    assert v["customer"] == "ПАО РОСТЕЛЕКОМ"
    assert v["status"] == "Идет прием заявок"
    assert v["purchase_type"] == "Открытый запрос цен"
    assert v["law"] == "223-ФЗ"
    assert v["publication_date"] is not None and v["publication_date"].tzinfo is not None
    assert v["deadline"] is not None and v["deadline"].tzinfo is not None
    assert v["_api"] == {"uuid": "abc123"}
    assert v["detail_path"] == "/procedure?procedureNumber=32616302720&lotNumber=1"


def test_mos_api_url_skip_offset_and_filter() -> None:
    """mos.ru: queryDto собирается из filter_json, skip — плейсхолдером {skip}."""
    platform = _make_mos_platform()
    url = build_api_list_url(platform, SearchCriteria(active_only=True), offset=0)
    q = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
    query_dto = json.loads(q[0][1])
    assert query_dto["skip"] == 0
    assert query_dto["take"] == "2"
    assert query_dto["filter"]["typeIn"]["values"] == [2]
    assert query_dto["filter"]["needSpecificFilter"]["stateIdIn"] == [20000002]

    url2 = build_api_list_url(platform, SearchCriteria(active_only=True), offset=10)
    query_dto2 = json.loads(urllib.parse.parse_qsl(urllib.parse.urlsplit(url2).query)[0][1])
    assert query_dto2["skip"] == 10


@pytest.mark.asyncio
async def test_crawl_api_rebuilds_url_with_offset(app_config: AppConfig) -> None:
    """Пагинация mos.ru: URL перестраивается с новым skip (offset) на каждой странице."""
    platform = _make_mos_platform(page_size=2)
    recorder = _make_lot_online_recorder(app_config, platform)
    page0 = [
        _mos_item("1", "Закупка А", "18.08.2026 13:56:07"),
        _mos_item("2", "Закупка Б", "17.08.2026 13:56:07"),
    ]
    page1 = [_mos_item("3", "Закупка В", "16.08.2026 13:56:07")]
    with patch(
        "zakupki_parser.parser.orchestrator.orchestrator.fetch_api_items",
        side_effect=[page0, page1],
    ) as fetch:
        await recorder._crawl_api(  # noqa: SLF001
            page=object(),  # type: ignore[arg-type]
            cutoff=datetime(2020, 1, 1, tzinfo=UTC),
            criteria=SearchCriteria(okpd_codes=["62.02"]),
            retry_cfg=RetryConfig(),
        )
    assert [r["number"] for r in recorder.processed] == ["1", "2", "3"]
    urls = [c.args[1] for c in fetch.await_args_list]
    assert len(urls) == 2
    assert "skip" in urls[0] and "skip" in urls[1]
    assert urls[0] != urls[1]
    dto0 = json.loads(urllib.parse.parse_qsl(urllib.parse.urlsplit(urls[0]).query)[0][1])
    dto1 = json.loads(urllib.parse.parse_qsl(urllib.parse.urlsplit(urls[1]).query)[0][1])
    assert dto0["skip"] == 0 and dto1["skip"] == 2
