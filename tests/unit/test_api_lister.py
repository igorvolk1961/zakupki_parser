"""Unit-тесты API-листера: построение запроса, парсинг item, обход _crawl_api."""

from __future__ import annotations

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
            keywords_sort="by_relevance",
            criteria_map={
                "keywords": CriteriaMapping(query_param="search"),
                "okpd2": CriteriaMapping(query_param="procedure[okpd]"),
                "active_only": CriteriaMapping(raw_array="procedure[stage]"),
            },
            state_ids={"active": ["accepting"]},
        ),
    )


def _params(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


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


def test_api_list_url_keywords_switches_sort_to_relevance() -> None:
    """При ключевых словах sort подменяется на keywords_sort (by_relevance)."""
    platform = _make_api_platform()
    url = build_api_list_url(platform, SearchCriteria(keywords=["искусственный интеллект"]))
    p = _params(url)
    assert p["search"] == "искусственный интеллект"
    assert p["sort"] == "by_relevance"


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
        keywords: list[str] | None = None,
    ) -> tuple[bool, Any]:
        self.processed.append(
            {"vars": list_vars, "url": detail_url, "number": number, "keywords": keywords}
        )
        return False, number


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
async def test_crawl_api_keywords_crawl_skips_cutoff(app_config: AppConfig) -> None:
    """Поиск по словам (relevance-сортировка) не останавливается по порогу дат."""
    platform = _make_api_platform(page_size=5)
    recorder = _make_recorder(app_config, platform)
    old = _item("OLD", "Старая", "2020-01-01T10:00:00.000+03:00")
    with patch(
        "zakupki_parser.parser.orchestrator.orchestrator.fetch_api_items",
        side_effect=[[old], []],
    ):
        await recorder._crawl_api(  # noqa: SLF001
            page=object(),  # type: ignore[arg-type]
            cutoff=datetime(2026, 8, 1, tzinfo=UTC),
            criteria=SearchCriteria(keywords=["искусственный интеллект"]),
            retry_cfg=RetryConfig(),
        )
    assert [r["number"] for r in recorder.processed] == ["OLD"]


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
async def test_run_splits_keywords_and_drops_short(app_config: AppConfig) -> None:
    """etpgpb: слова перебираются по одному, короткие (< min_keyword_len) отбрасываются.

    «ИИ» (2 символа) не должен попасть в поисковые обходы — поиск etpgpb по коротким
    словам возвращает нерелевантные закупки (проверено 2026-08-18).
    """
    platform = _make_api_platform()
    assert platform.search is not None
    platform.search.min_keyword_len = 3
    cfg = app_config.model_copy(deep=True)
    cfg.service.search_criteria.keywords = ["искусственный интеллект", "ИИ", "автоматизация"]
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

    assert [c.keywords for c in recorder.crawled] == [
        ["искусственный интеллект"],
        ["автоматизация"],
        [],  # отдельный обход по кодам ОКПД2
    ]
