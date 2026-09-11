"""Настройки analysis_service (RAG-анализ стоп-условий).

Порядок приоритета (от высшего к низшему):
1. аргументы конструктора;
2. переменные окружения ``ANALYSIS_*``;
3. собственный ``.env`` сервиса (каталог ``analysis_service/``);
4. YAML-конфиг (по умолчанию ``config.yaml``, путь — env ``ANALYSIS_CONFIG_FILE``);
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
from scoring_common.geo.geocoder import GeocodingConfig
from scoring_common.giga import (
    GIGA_AUTH_SCOPE,
    GIGA_AUTH_URL,
    GIGA_BASE_URL,
    GIGA_DEFAULT_MIN_TOKEN_TTL_SECONDS,
    GIGA_DEFAULT_TIMEOUT_SECONDS,
    GIGA_EMBEDDINGS_MODEL,
)
from scoring_common.logging import LoggingSettings

# Собственный каталог сервиса: src/analysis_service/analysis_service/settings.py -> parents[1].
_SERVICE_DIR = Path(__file__).resolve().parents[1]


class _YamlSource(YamlConfigSource):
    """YAML-источник с фиксированным путём из env ``ANALYSIS_CONFIG_FILE``."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        path = Path(os.getenv("ANALYSIS_CONFIG_FILE", "config.yaml"))
        super().__init__(settings_cls, path)


class Settings(BaseSettings):
    """Конфигурация сервиса RAG-анализа."""

    model_config = SettingsConfigDict(
        env_prefix="ANALYSIS_",
        env_file=_SERVICE_DIR / ".env",
        extra="ignore",
        env_nested_delimiter="__",
    )

    # LLM (OpenAI-совместимый) для верификации стоп-условий в найденных чанках.
    llm_base_url: str = "http://localhost:8001/v1"
    llm_api_key: str = "sk-dummy"
    llm_model: str = "deepseek-chat"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.0
    llm_request_timeout: float = 45.0

    # Эмбеддинги: прямой Giga Embedder (модель EmbeddingsGigaR, автообновление
    # OAuth-токена) — те же модель и ключи доступа, что и у scoring_service.
    # Если ключ доступа не задан — фолбэк на OpenAI-совместимый endpoint /embeddings
    # (embedding_base_url, Giga через gpt2giga-прокси).
    giga_enabled: bool = True
    giga_base_url: str = GIGA_BASE_URL
    giga_embeddings_model: str = GIGA_EMBEDDINGS_MODEL
    giga_auth_url: str = GIGA_AUTH_URL
    giga_client_id: str = ""
    giga_client_secret: str = ""
    giga_auth_scope: str = GIGA_AUTH_SCOPE
    giga_timeout_seconds: float = GIGA_DEFAULT_TIMEOUT_SECONDS
    giga_min_token_ttl_seconds: float = GIGA_DEFAULT_MIN_TOKEN_TTL_SECONDS
    # Проверять SSL-сертификат при обращении к Giga. OAuth (ngw...:9443) использует
    # самоподписанный сертификат — для локальной разработки выключено (см. .env).
    giga_verify_ssl: bool = True

    # Фолбэк: OpenAI-совместимый endpoint /embeddings (Giga через gpt2giga-прокси),
    # используется, если ключ доступа Giga не задан.
    embedding_base_url: str = "http://localhost:8002/v1"
    embedding_api_key: str | None = None
    embedding_model: str = "EmbeddingsGigaR"
    embedding_timeout: float = 30.0

    # Парсер закупок (REST, без БД).
    parser_api_url: str = "http://localhost:8000"
    # Внутренний токен парсера для служебных эндпоинтов (GET /api/clients/active).
    # Из env ANALYSIS_PARSER_INTERNAL_TOKEN.
    parser_internal_token: str | None = None
    parser_retry_backoff_seconds: float = 5.0

    # Redis-очередь.
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "analysis:jobs"
    results_key: str = "analysis:results"
    processing_key: str = "analysis:processing"
    processing_meta_key: str = "analysis:processing_meta"
    processing_ttl_seconds: int = 600
    processing_recovery_priority: float = 0.0
    queue_poll_seconds: float = 2.0
    jobs_retry_key: str = "analysis:jobs_retries"

    @property
    def giga_configured(self) -> bool:
        """Ключ доступа Giga задан (можно выполнять эмбеддинги напрямую)."""
        return bool(self.giga_client_id and self.giga_client_secret)

    @property
    def geo_config(self) -> GeocodingConfig:
        """Конфиг геокодера этапа анализа (ключ — из env ``ANALYSIS_GEO_API_KEY``)."""
        return GeocodingConfig(
            enabled=self.geo_enabled,
            provider=self.geo_provider,
            base_url=self.geo_base_url,
            min_result_quality=self.geo_min_result_quality,
            timeout_seconds=self.geo_timeout_seconds,
            max_retries=self.geo_max_retries,
            retry_backoff_seconds=self.geo_retry_backoff_seconds,
            rate_limit_per_second=self.geo_rate_limit_per_second,
            user_agent=self.geo_user_agent,
            key_env="ANALYSIS_GEO_API_KEY",
        )

    # Геокодирование (этап анализа: место поставки не дальше N км от центра региона).
    # Провайдер/доступ — как у парсера ранее; ключ читается из env ``ANALYSIS_GEO_API_KEY``.
    geo_enabled: bool = False
    geo_provider: str = "dadata"
    # Нейтральный default; рабочий URL провайдера задаётся в config.yaml
    # (geo_base_url) или env ANALYSIS_GEO_BASE_URL.
    geo_base_url: str = "https://suggestions.dadata.example/suggestions/api/4_1/rs/suggest/address"
    geo_min_result_quality: int = Field(default=1, ge=0, le=4)
    geo_timeout_seconds: float = Field(default=10.0, gt=0)
    geo_max_retries: int = Field(default=2, ge=0)
    geo_retry_backoff_seconds: float = Field(default=2.0, ge=0)
    geo_rate_limit_per_second: float = Field(default=1.0, gt=0)
    geo_user_agent: str = "zakupki-parser/0.5"

    # RAG-параметры.
    chunk_max_chars: int = Field(default=1500, ge=200, description="макс. размер чанка (символов)")
    top_k: int = Field(default=3, ge=1, description="сколько чанков отдавать LLM на вопрос")
    tz_download_timeout: float = 30.0
    # Проверять SSL-сертификат при скачивании файла ТЗ. Площадки за TLS-перехватом
    # (VPN/корп. прокси) отдают самоподписанный промежуточный сертификат, которому
    # httpx не доверяет — поэтому по умолчанию выключено (см. scoring_service.tz_verify_ssl).
    tz_verify_ssl: bool = False

    # Логирование (собственный блок config.yaml; env — ANALYSIS_LOGGING__LEVEL и т.п.).
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
