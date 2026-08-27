"""Модель инфраструктурной (devops) конфигурации scoring_service (config_score_ops.yaml).

Здесь — параметры подключений, провайдеров, очередей и аварийных переключателей
scoring_service. Бизнес-правила оценки (пороги/веса) управляются аналитиком в
``config_service.yaml -> scoring`` (см. ``models/service.py``). Секреты
(``llm_api_key``, ``giga_client_id``/``giga_client_secret``, ``parser_internal_token``,
``auth_token``, LangFuse-ключи) в файл/форму НЕ попадают — задаются через env
(Secret в k8s).
"""

from __future__ import annotations

from pydantic import Field

from zakupki_parser.config.models.ops.base import _BaseConfig


class ScoringOpsConfig(_BaseConfig):
    """Инфраструктурная конфигурация scoring_service (devops)."""

    # LLM (OpenAI-совместимый)
    llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Базовый URL LLM (OpenAI-совместимый)",
    )
    llm_model: str = Field(default="gpt-4o-mini", description="Модель LLM")
    llm_temperature: float = Field(default=0.0, ge=0, le=2, description="Температура LLM")
    llm_request_timeout: float = Field(
        default=45.0, gt=0, description="Таймаут одного LLM-запроса (сек)"
    )
    llm_max_retries: int = Field(
        default=1, ge=0, description="Повторы LLM-запроса при сетевой ошибке/таймауте"
    )
    llm_retry_max_attempts: int = Field(
        default=3, ge=1, description="Макс. возвратов задачи в очередь при сбое LLM"
    )
    llm_retry_backoff_seconds: float = Field(
        default=5.0,
        ge=0,
        description="Пауза перед повторной обработкой задачи после сбоя LLM (сек)",
    )

    # Парсер закупок (REST)
    parser_retry_backoff_seconds: float = Field(
        default=5.0, ge=0, description="Пауза при недоступности парсера (сек)"
    )

    # Аварийный переключатель
    score_use_stub: bool = Field(
        default=False,
        description="Вернуть score из карточки без LLM-пайплайна (аварийный переключатель)",
    )

    # Giga Embedder (ветка векторной близости)
    giga_base_url: str = Field(
        default="https://gigachat.devices.sberbank.ru/api/v1",
        description="Базовый URL Giga Embedder",
    )
    giga_embeddings_model: str = Field(
        default="EmbeddingsGigaR", description="Модель эмбеддингов Giga"
    )
    giga_auth_url: str = Field(
        default="https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        description="URL OAuth-авторизации Giga",
    )
    giga_auth_scope: str = Field(
        default="GIGACHAT_API_PERS", description="Scope OAuth-авторизации Giga"
    )
    giga_timeout_seconds: float = Field(
        default=30.0, gt=0, description="Таймаут запроса эмбеддингов (сек)"
    )
    giga_min_token_ttl_seconds: float = Field(
        default=60.0, ge=0, description="Мин. TTL токена до обновления (сек)"
    )
    giga_verify_ssl: bool = Field(
        default=True, description="Проверять SSL-сертификат при обращении к Giga"
    )
