"""Настройки сервиса Margin.

Порядок приоритета (от высшего к низшему):
1. аргументы конструктора;
2. переменные окружения ``MARGIN_*``;
3. файл ``.env``;
4. YAML-конфиг (по умолчанию ``config.yaml``, путь можно переопределить env
   ``MARGIN_CONFIG_FILE``);
5. значения по умолчанию в модели.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from scoring_common.config import YamlConfigSource


class _YamlSource(YamlConfigSource):
    """YAML-источник с фиксированным путём из env ``MARGIN_CONFIG_FILE``."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        path = Path(os.getenv("MARGIN_CONFIG_FILE", "config.yaml"))
        super().__init__(settings_cls, path)


class Settings(BaseSettings):
    """Конфигурация сервиса Margin."""

    model_config = SettingsConfigDict(env_prefix="MARGIN_", env_file=".env", extra="ignore")

    # Парсер закупок (REST, без БД)
    parser_api_url: str = "http://localhost:8000"
    # Пауза перед повторной обработкой задачи при недоступности парсера (сек).
    parser_retry_backoff_seconds: float = 5.0

    # Redis-очередь
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "margin:jobs"
    results_key: str = "margin:results"
    processing_key: str = "margin:processing"
    processing_meta_key: str = "margin:processing_meta"
    processing_ttl_seconds: int = 600
    processing_recovery_priority: float = 0.0
    queue_poll_seconds: float = 2.0
    # Счётчик ретраев задач (HASH): см. scoring_service.settings (общий StageQueue).
    jobs_retry_key: str = "margin:jobs_retries"

    # Норма прибыли: Margin = НМЦК × margin_rate
    margin_rate: float = 1.0

    # Пайплайн
    score_round_digits: int = 2

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
