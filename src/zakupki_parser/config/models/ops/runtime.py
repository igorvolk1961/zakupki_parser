"""Корневая эксплуатационная конфигурация (config_ops.yaml)."""

from __future__ import annotations

from pydantic import Field

from zakupki_parser.config.models.ops.auth import AuthConfig
from zakupki_parser.config.models.ops.base import _BaseConfig
from zakupki_parser.config.models.ops.db import DbConfig
from zakupki_parser.config.models.ops.notifications import NotificationsConfig


class OpsConfig(_BaseConfig):
    """Эксплуатационная конфигурация: таймер, БД, уведомления, выгрузка, circuit breaker."""

    timeout_seconds: int = Field(default=3600, ge=1)
    db: DbConfig = Field(default_factory=DbConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    export_dir: str = Field(
        default="data/export",
        description=(
            "каталог на сервере для выгрузки БД в CSV (кнопка «Выгрузить CSV»); "
            "создаётся автоматически при выгрузке"
        ),
    )
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout_seconds: float = Field(default=60.0, ge=1)
    prompts_dir: str = Field(
        default="src/scoring_service/scoring_service/pipeline/prompts",
        description=(
            "каталог с файлами промптов scoring_service (вкладка «Промпты»); "
            "относительный путь — от корня проекта; в Docker — общий том, "
            "переопределяется через ZAKUPKI_PROMPTS_DIR"
        ),
    )
    analysis_prompts_dir: str = Field(
        default="src/analysis_service/analysis_service/pipeline/prompts",
        description=(
            "каталог с файлами промптов analysis_service (вкладка «Промпты»); "
            "относительный путь — от корня проекта; в Docker — общий том, "
            "переопределяется через ZAKUPKI_ANALYSIS_PROMPTS_DIR"
        ),
    )
