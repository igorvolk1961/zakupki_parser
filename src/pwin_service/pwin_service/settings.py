"""Настройки сервиса P(win).

Порядок приоритета (от высшего к низшему):
1. аргументы конструктора;
2. переменные окружения ``PWIN_*``;
3. файл ``.env``;
4. YAML-конфиг (по умолчанию ``config.yaml``, путь можно переопределить env
   ``PWIN_CONFIG_FILE``);
5. значения по умолчанию в модели.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from scoring_common.config import PwinCoefficients, YamlConfigSource


class _YamlSource(YamlConfigSource):
    """YAML-источник с фиксированным путём из env ``PWIN_CONFIG_FILE``."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        path = Path(os.getenv("PWIN_CONFIG_FILE", "config.yaml"))
        super().__init__(settings_cls, path)


class Settings(BaseSettings, PwinCoefficients):
    """Конфигурация сервиса P(win)."""

    model_config = SettingsConfigDict(env_prefix="PWIN_", env_file=".env", extra="ignore")

    # Парсер закупок (REST, без БД)
    parser_api_url: str = "http://localhost:8000"
    # Пауза перед повторной обработкой задачи при недоступности парсера (сек).
    parser_retry_backoff_seconds: float = 5.0

    # Redis-очередь
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "pwin:jobs"
    results_key: str = "pwin:results"
    processing_key: str = "pwin:processing"
    processing_meta_key: str = "pwin:processing_meta"
    processing_ttl_seconds: int = 600
    processing_recovery_priority: float = 0.0
    queue_poll_seconds: float = 2.0
    # Счётчик ретраев задач (HASH): см. scoring_service.settings (общий StageQueue).
    jobs_retry_key: str = "pwin:jobs_retries"

    # Пайплайн
    score_round_digits: int = 4

    # Заглушка: константное P(win) без расчёта по карточке (модель калибруется).
    # Включать, пока модель коэффициентов не отлажена. AliasChoices: из-за
    # env_prefix="PWIN_" без явного алиаса pydantic ждёт PWIN_use_stub.
    use_stub: bool = Field(
        default=False, validation_alias=AliasChoices("use_stub", "PWIN_USE_STUB")
    )
    # Константа P(win) в режиме заглушки (0..1).
    stub_pwin: float = Field(default=0.5, ge=0, le=1)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Источники в порядке приоритета (первый — самый высокий).
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSource(settings_cls),
            file_secret_settings,
        )


def get_settings() -> Settings:
    return Settings()
