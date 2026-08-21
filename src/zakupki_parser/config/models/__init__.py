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
    CriteriaMapping,
    DomConfig,
    DomDetailConfig,
    DomListConfig,
    DomVariable,
    FileSpec,
    FilterMapping,
    FilterStep,
    OrganizationConfig,
    PlatformDom,
    PurchaseFilter,
    SearchFilterConfig,
    SortConfig,
)
from zakupki_parser.config.models.logging import LoggingConfig
from zakupki_parser.config.models.ops import (
    DbConfig,
    MaxConfig,
    NotificationsConfig,
    OpsConfig,
    TelegramConfig,
    WebhookConfig,
)
from zakupki_parser.config.models.parser import (
    BrowserConfig,
    ParserConfig,
    RequestLimits,
    RetryConfig,
)
from zakupki_parser.config.models.score import (
    SCORE_METHOD_FIT,
    SCORE_METHOD_MARGIN,
    SCORE_METHOD_PWIN,
    SCORE_METHOD_SIM,
    SCORE_METHOD_STAGES,
    ScoreConfig,
)
from zakupki_parser.config.models.service import (
    SearchCriteria,
    ServiceConfig,
    SiteServiceEntry,
    StopConditions,
)

__all__ = [
    "AppConfig",
    "BrowserConfig",
    "CriteriaMapping",
    "DbConfig",
    "DomConfig",
    "DomDetailConfig",
    "DomListConfig",
    "DomVariable",
    "FileSpec",
    "FilterMapping",
    "FilterStep",
    "LoggingConfig",
    "MaxConfig",
    "NotificationsConfig",
    "OpsConfig",
    "OrganizationConfig",
    "ParserConfig",
    "PlatformDom",
    "PurchaseFilter",
    "RequestLimits",
    "RetryConfig",
    "SCORE_METHOD_FIT",
    "SCORE_METHOD_MARGIN",
    "SCORE_METHOD_PWIN",
    "SCORE_METHOD_SIM",
    "SCORE_METHOD_STAGES",
    "ScoreConfig",
    "SearchCriteria",
    "SearchFilterConfig",
    "ServiceConfig",
    "SiteServiceEntry",
    "SortConfig",
    "StopConditions",
    "TelegramConfig",
    "WebhookConfig",
]


class AppConfig(BaseModel):
    """Собирает все конфиги вместе для удобной передачи."""

    configs_dir: Path
    parser: ParserConfig
    dom: DomConfig
    service: ServiceConfig
    ops: OpsConfig = Field(default_factory=OpsConfig)
    logging: LoggingConfig
    score: ScoreConfig = Field(default_factory=ScoreConfig)
