"""Pydantic-модели конфигурации парсера.

Все параметры парсера задаются исключительно через YAML-файлы в ``configs/``.
Эти модели валидируют загруженный конфиг и предоставляют типизированный доступ.

Модели разбиты по доменам конфигов (подпакеты): ``parser``, ``dom``, ``service``,
``logging``. Здесь — реэкспорт и корневая модель ``AppConfig``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from zakupki_parser.config.models.dom import (
    DomConfig,
    DomDetailConfig,
    DomListConfig,
    DomVariable,
    FilterStep,
    PlatformDom,
    PurchaseFilter,
    SearchFilterConfig,
    SortConfig,
)
from zakupki_parser.config.models.logging import LoggingConfig
from zakupki_parser.config.models.parser import (
    BrowserConfig,
    ParserConfig,
    RequestLimits,
    RetryConfig,
)
from zakupki_parser.config.models.service import (
    DbConfig,
    SearchCriteria,
    ServiceConfig,
    SiteServiceEntry,
    StopConditions,
    WebhookConfig,
)

__all__ = [
    "AppConfig",
    "BrowserConfig",
    "DbConfig",
    "DomConfig",
    "DomDetailConfig",
    "DomListConfig",
    "DomVariable",
    "FilterStep",
    "LoggingConfig",
    "ParserConfig",
    "PlatformDom",
    "PurchaseFilter",
    "RequestLimits",
    "RetryConfig",
    "SearchCriteria",
    "SearchFilterConfig",
    "ServiceConfig",
    "SiteServiceEntry",
    "SortConfig",
    "StopConditions",
    "WebhookConfig",
]


class AppConfig(BaseModel):
    """Собирает все конфиги вместе для удобной передачи."""

    configs_dir: Path
    parser: ParserConfig
    dom: DomConfig
    service: ServiceConfig
    logging: LoggingConfig
