"""Настройки indexing_service.

Порядок приоритета (от высшего к низшему):
1. аргументы конструктора;
2. переменные окружения ``INDEX_*``;
3. файл ``.env``;
4. YAML-конфиг (по умолчанию ``config.yaml``, путь — env ``INDEX_CONFIG_FILE``);
5. значения по умолчанию в модели.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from scoring_common.config import YamlConfigSource
from scoring_common.logging import LoggingSettings


class _YamlSource(YamlConfigSource):
    """YAML-источник с фиксированным путём из env ``INDEX_CONFIG_FILE``."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        path = Path(os.getenv("INDEX_CONFIG_FILE", "config.yaml"))
        super().__init__(settings_cls, path)


class Settings(BaseSettings):
    """Конфигурация сервиса фоновой индексации."""

    model_config = SettingsConfigDict(
        env_prefix="INDEX_", env_file=".env", extra="ignore", env_nested_delimiter="__"
    )

    # Парсер закупок (REST, без БД) — те же соглашения, что и у соседних стадий.
    parser_api_url: str = "http://localhost:8000"
    parser_internal_token: str | None = None
    parser_retry_backoff_seconds: float = 5.0

    # Redis-очередь (ключи должны совпадать с scoring_transport.settings.Settings
    # index_jobs_key/index_results_key — единая точка правды: docker/.env).
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "index:jobs"
    results_key: str = "index:results"
    processing_key: str = "index:processing"
    processing_meta_key: str = "index:processing_meta"
    processing_ttl_seconds: int = 600
    processing_recovery_priority: float = 0.0
    queue_poll_seconds: float = 2.0
    jobs_retry_key: str = "index:jobs_retries"

    # Извлечение текста документов (scoring_common.tz): ограничения — защита от
    # неконтролируемого роста нагрузки/памяти на один патологический пакет
    # документов (архив с сотнями файлов), см. риски плана индексации.
    max_files_per_procurement: int = 20
    max_document_chars: int = 200_000
    download_timeout_seconds: float = 30.0
    # Площадки за TLS-перехватом отдают самоподписанный сертификат — по умолчанию
    # выключено (как у scoring_service/analysis_service, см. tz_verify_ssl).
    verify_ssl: bool = False

    # Своя «вежливость» — дозагрузка деталей/файлов сегодня не ограничена никаким
    # Delayer (см. риск №3 плана индексации): собственный лимит конкурентности +
    # пауза между файлами одной закупки, независимо от парсера/analysis_service.
    max_concurrent_downloads: int = 2
    download_delay_seconds: float = 1.0

    # Логирование (собственный блок config.yaml; env — INDEX_LOGGING__LEVEL и т.п.).
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSource(settings_cls),
            file_secret_settings,
        )


def get_settings() -> Settings:
    return Settings()
