"""Unit-тесты обработки ключевых слов в обходе площадки (min_keyword_len)."""

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
async def test_min_keyword_len_drops_short_words(app_config: AppConfig) -> None:
    """Площадка с min_keyword_len не ищет по коротким словам (fabrikant: «ИИ»)."""
    recorder = _make_recorder(app_config)
    search = recorder._platform.search  # noqa: SLF001
    assert search is not None
    search.min_keyword_len = 3

    await recorder.run(page=object())  # type: ignore[arg-type]

    assert [c.keywords for c in recorder.crawled] == [
        ["искусственный интеллект"],
        ["автоматизация"],
        [],  # отдельный обход по кодам ОКПД2
    ]


@pytest.mark.asyncio
async def test_no_min_keyword_len_keeps_short_words(app_config: AppConfig) -> None:
    """Без min_keyword_len (площадка не игнорирует короткие слова) — как раньше."""
    recorder = _make_recorder(app_config)

    await recorder.run(page=object())  # type: ignore[arg-type]

    assert [c.keywords for c in recorder.crawled] == [
        ["искусственный интеллект"],
        ["ИИ"],
        ["автоматизация"],
        [],  # отдельный обход по кодам ОКПД2
    ]
