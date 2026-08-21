"""Unit-тесты серверного обхода по ОКПД2 и клиентской фильтрации словами (R9).

По требованию заказчика ключевые слова НЕ передаются на площадку: серверная
фильтрация — только по кодам ОКПД2 (+ обход «без кода»); позитивные/негативные
слова применяются клиентски до записи в БД (см. test_filtering.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from playwright.async_api import Page

from zakupki_parser.config.models import AppConfig, RetryConfig, SearchCriteria
from zakupki_parser.parser.orchestrator import Orchestrator


class _OkCircuit:
    def allow_request(self) -> bool:
        return True

    def record_success(self) -> None:
        pass


class _Recorder(Orchestrator):
    """Перехватывает _crawl и записывает критерии каждого поискового обхода."""

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


def _make_recorder(app_config: AppConfig) -> _Recorder:
    cfg = app_config.model_copy(deep=True)
    cfg.service.search_criteria.keywords = ["искусственный интеллект", "ИИ", "автоматизация"]
    return _Recorder(
        cfg=cfg,
        platform_id="zakupki_mos",
        platform=cfg.dom.platforms["zakupki_mos"],
        delayer=object(),
        repository=None,
        notifier=None,
        site_cb=_OkCircuit(),
        db_cb=_OkCircuit(),
        now=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_server_crawl_has_no_keywords(app_config: AppConfig) -> None:
    """R9: в серверных запросах нет ключевых слов — только коды ОКПД2."""
    recorder = _make_recorder(app_config)
    await recorder.run(page=object())  # type: ignore[arg-type]

    assert recorder.crawled
    assert all(c.keywords == [] for c in recorder.crawled)
    # Обход выполняется по кодам ОКПД2 из search_criteria.
    assert all(c.okpd_codes == ["62.02"] for c in recorder.crawled)


@pytest.mark.asyncio
async def test_no_code_crawl_skipped_without_profile(app_config: AppConfig) -> None:
    """Без активного профиля (repository=None) обход «без кода» не выполняется.

    Обход «без кода» нужен только при наличии позитивных ключевых слов в профиле —
    иначе он бессмыслен (отбирать не по чему): пропускается с записью в лог.
    """
    recorder = _make_recorder(app_config)
    await recorder.run(page=object())  # type: ignore[arg-type]

    # Только обход по кодам; никаких обходов с пустым набором кодов.
    assert [c.okpd_codes for c in recorder.crawled] == [["62.02"]]
