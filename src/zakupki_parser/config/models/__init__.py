"""Pydantic-модели конфигурации парсера.

Все параметры парсера задаются исключительно через YAML-файлы в ``configs/``.
Эти модели валидируют загруженный конфиг и предоставляют типизированный доступ.

Модели разбиты по доменам конфигов (подпакеты): ``parser``, ``dom``, ``service``,
``logging``. Здесь — реэкспорт и корневая модель ``AppConfig``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from zakupki_parser.config.models.dom import (
    DomConfig,
    DomDetailConfig,
    DomListConfig,
    DomVariable,
    FileSpec,
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
from zakupki_parser.config.models.score import (
    SCORE_METHOD_CALCULATING,
    SCORE_METHOD_DEADLINE_EXPIRED,
    SCORE_METHOD_DEFAULT,
    SCORE_METHOD_EXTERNAL,
    ScoreConfig,
)
from zakupki_parser.config.models.service import (
    DbConfig,
    SearchCriteria,
    ServiceConfig,
    SiteServiceEntry,
    StopConditions,
    StorageConfig,
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
    "FileSpec",
    "FilterStep",
    "LoggingConfig",
    "ParserConfig",
    "PlatformDom",
    "PurchaseFilter",
    "RequestLimits",
    "RetryConfig",
    "SCORE_METHOD_CALCULATING",
    "SCORE_METHOD_DEADLINE_EXPIRED",
    "SCORE_METHOD_DEFAULT",
    "SCORE_METHOD_EXTERNAL",
    "ScoreConfig",
    "SearchCriteria",
    "SearchFilterConfig",
    "ServiceConfig",
    "SiteServiceEntry",
    "SortConfig",
    "StorageConfig",
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
    score: ScoreConfig = Field(default_factory=ScoreConfig)
