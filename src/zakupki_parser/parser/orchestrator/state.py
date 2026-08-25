"""Общее состояние Orchestrator, видимое всем миксинам (типизация).

``OrchestratorState`` объявляет атрибуты, задаваемые в ``Orchestrator.__init__``,
и методы, определённые в других миксинах/базовом классе. Нужен, чтобы mypy
понимал обращения к общему состоянию из миксинов ``activity``/``persistence``/
``stop``/``processing``/``crawl``. Методы-заглушки переопределяются реальными
реализациями в самих миксинах и в классе ``Orchestrator``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.circuit import CircuitBreaker
from zakupki_parser.config.models import AppConfig, PlatformDom, SearchCriteria
from zakupki_parser.notify import Notifier
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import Profile
from zakupki_parser.storage.repository import ProcurementRepository


class OrchestratorState:
    """Состояние прохода одной площадки: конфигурация, БД, колбэки, кеши."""

    _cfg: AppConfig
    _platform_id: str
    _platform: PlatformDom
    _delayer: Delayer
    _repository: ProcurementRepository | None
    _notifier: Notifier
    _site_cb: CircuitBreaker
    _db_cb: CircuitBreaker
    _new_page: Callable[[], Awaitable[Page]] | None
    _now: datetime
    _on_record_saved: Callable[[], Awaitable[None]] | None
    _transport: ScoringTransportClient | None
    _inn_cache: dict[str, str | None]
    _known_numbers: set[str] | None
    _client_profile: Profile | None
    _client_keywords: list[str]
    _client_exclusion_words: list[str]
    _platform_stats: dict[str, int]
    _normalized_active_statuses: set[str]

    # Методы, определённые в других миксинах/базовом классе (типизация).

    def _is_active(self, record: dict[str, Any]) -> bool:
        raise NotImplementedError

    def _check_stop_conditions(self, record: dict[str, Any]) -> bool:
        raise NotImplementedError

    async def _persist(self, record: dict[str, Any]) -> bool:
        raise NotImplementedError

    async def _resolve_customer_inn(self, page: Page, customer_link: str | None) -> str | None:
        raise NotImplementedError

    async def _process_container(self, page: Page, container: Locator) -> tuple[bool, Any, bool]:
        raise NotImplementedError

    def _is_known(self, number: Any) -> bool:
        raise NotImplementedError

    async def _process_list_record(
        self,
        page: Page,
        list_vars: dict[str, Any],
        detail_url: str | None,
        number: Any,
        api_fields: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, bool]:
        raise NotImplementedError

    def _log_crawl_summary(
        self,
        criteria: SearchCriteria,
        received: int,
        saved: int,
        known: int,
    ) -> None:
        raise NotImplementedError
