"""Модели конфигураций фоновых сервисов (devops, вкладка «Сервисы»).

Каждый сервис читает собственный ``src/<service>/config.yaml`` (плюс ``.env`` и
env ``SERVICE_*`` как приоритетные источники). Здесь — несекретная часть этой
конфигурации для веб-формы по образцу инфраструктурных конфигов парсера
(``_BaseConfig``: неизвестные ключи — ошибка).

Секреты (``llm_api_key``, ``giga_client_id``/``giga_client_secret``,
``parser_internal_token``, ``auth_token``, LangFuse-ключи) в модели и в форме
НЕ отображаются: они управляются через ``.env`` сервиса и env ``SERVICE_*``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from zakupki_parser.config.models.ops.base import _BaseConfig


class ScoringServiceConfig(_BaseConfig):
    """Несекретная конфигурация scoring_service (src/scoring_service/config.yaml)."""

    # LLM (OpenAI-совместимый) — совпадает с фактом запуска (см. .env сервиса).
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="Базовый URL LLM (OpenAI-совместимый)",
    )
    llm_model: str = Field(default="deepseek-v4-flash", description="Модель LLM")
    llm_temperature: float = Field(default=0.0, ge=0, le=2, description="Температура LLM")
    llm_request_timeout: float = Field(
        default=45.0, gt=0, description="Таймаут одного LLM-запроса (сек)"
    )
    llm_max_retries: int = Field(
        default=1, ge=0, description="Повторы LLM-запроса при сетевой ошибке/таймауте"
    )
    llm_structured_method: Literal["json_mode", "json_schema", "function_calling"] = Field(
        default="json_mode",
        description="Способ строгого JSON-выхода (json_mode — совместимо с DeepSeek)",
    )

    # Парсер закупок (REST, без прямого доступа к БД)
    parser_api_url: str = Field(default="http://localhost:8000", description="URL REST API парсера")
    parser_retry_backoff_seconds: float = Field(
        default=5.0, ge=0, description="Пауза при недоступности парсера (сек)"
    )

    # Redis-очередь
    redis_url: str = Field(default="redis://localhost:6379/0", description="URL Redis")
    jobs_key: str = Field(default="scoring:jobs", description="Ключ очереди задач")
    results_key: str = Field(default="scoring:results", description="Ключ результатов")
    processing_key: str = Field(default="scoring:processing", description="Ключ обработки")
    processing_meta_key: str = Field(
        default="scoring:processing_meta", description="Ключ меты обработки"
    )
    processing_ttl_seconds: int = Field(default=600, ge=1, description="TTL аренды задачи (сек)")
    processing_recovery_priority: float = Field(
        default=0.0, description="Приоритет восстановления зависших задач"
    )
    queue_poll_seconds: float = Field(default=2.0, gt=0, description="Период опроса очереди (сек)")
    jobs_retry_key: str = Field(default="scoring:jobs_retries", description="Ключ счётчика ретраев")

    # Повторы при транзиентных ошибках LLM-провайдера
    llm_retry_max_attempts: int = Field(
        default=3, ge=1, description="Макс. возвратов задачи в очередь при сбое LLM"
    )
    llm_retry_backoff_seconds: float = Field(
        default=5.0, ge=0, description="Пауза перед повторной обработкой после сбоя LLM (сек)"
    )

    # Профиль поставщика
    competencies_file: str = Field(
        default="data/profile.yaml", description="Файл профиля поставщика (YAML/JSON)"
    )

    # Пайплайн
    num_refine_rounds: int = Field(default=1, ge=0, description="Итерации refine при reject")
    max_fit_score: float = Field(default=10.0, description="Максимальный Fit")
    min_fit_score: float = Field(default=0.0, description="Минимальный Fit")
    score_round_digits: int = Field(default=2, ge=0, description="Округление score")
    normalize_fit_for_score: bool = Field(
        default=True, description="Приводить Fit (0–10) к шкале 0–1 при расчёте Score"
    )

    # Уточнение по тексту ТЗ
    tz_review_enabled: bool = Field(
        default=True, description="Уточнять score по тексту ТЗ (requires_tz_review)"
    )
    tz_download_timeout: float = Field(
        default=30.0, gt=0, description="Таймаут скачивания ТЗ (сек)"
    )
    tz_verify_ssl: bool = Field(default=True, description="Проверять SSL при скачивании файла ТЗ")

    # Ветка векторной близости (Giga Embedder) — включена по факту запуска (.env).
    giga_enabled: bool = Field(default=True, description="Включить ветку векторной близости")
    giga_base_url: str = Field(
        default="https://gigachat.devices.sberbank.ru/api/v1", description="Базовый URL Giga"
    )
    giga_embeddings_model: str = Field(default="EmbeddingsGigaR", description="Модель эмбеддингов")
    giga_auth_url: str = Field(
        default="https://ngw.devices.sberbank.ru:9443/api/v2/oauth", description="URL OAuth Giga"
    )
    giga_auth_scope: str = Field(default="GIGACHAT_API_PERS", description="Scope OAuth Giga")
    giga_embedding_alpha: float = Field(
        default=0.0, description="Влияние ветки на score (0 — только диагностика)"
    )
    giga_timeout_seconds: float = Field(default=30.0, gt=0, description="Таймаут эмбеддингов (сек)")
    giga_min_token_ttl_seconds: float = Field(
        default=60.0, ge=0, description="Мин. TTL токена до обновления (сек)"
    )
    giga_verify_ssl: bool = Field(default=False, description="Проверять SSL при обращении к Giga")
    embedding_filter_threshold: float = Field(
        default=0.0,
        description="Порог векторной близости (<= 0 — фильтрация выключена)",
    )

    # Аварийный переключатель
    score_use_stub: bool = Field(
        default=False,
        description="Вернуть score из карточки без LLM-пайплайна (аварийный переключатель)",
    )

    # Дедлайн на одну закупку
    eval_item_timeout_seconds: float = Field(
        default=300.0, gt=0, description="Дедлайн на оценку одной закупки (сек)"
    )


class AnalysisServiceConfig(_BaseConfig):
    """Несекретная конфигурация analysis_service (src/analysis_service/config.yaml)."""

    # LLM (OpenAI-совместимый) — та же модель и ключ, что и в scoring_service (#5).
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1", description="Базовый URL LLM (OpenAI-совместимый)"
    )
    llm_model: str = Field(default="deepseek-v4-flash", description="Модель LLM")
    llm_temperature: float = Field(default=0.0, ge=0, le=2, description="Температура LLM")
    llm_request_timeout: float = Field(
        default=45.0, gt=0, description="Таймаут одного LLM-запроса (сек)"
    )

    # Эмбеддинги (OpenAI-совместимый endpoint /embeddings)
    embedding_base_url: str = Field(
        default="http://localhost:8002/v1", description="Базовый URL эмбеддингов"
    )
    embedding_model: str = Field(default="EmbeddingsGigaR", description="Модель эмбеддингов")
    embedding_timeout: float = Field(default=30.0, gt=0, description="Таймаут эмбеддингов (сек)")

    # Парсер закупок (REST, без БД)
    parser_api_url: str = Field(default="http://localhost:8000", description="URL REST API парсера")
    parser_retry_backoff_seconds: float = Field(
        default=5.0, ge=0, description="Пауза при недоступности парсера (сек)"
    )

    # Redis-очередь
    redis_url: str = Field(default="redis://localhost:6379/0", description="URL Redis")
    jobs_key: str = Field(default="analysis:jobs", description="Ключ очереди задач")
    results_key: str = Field(default="analysis:results", description="Ключ результатов")
    processing_key: str = Field(default="analysis:processing", description="Ключ обработки")
    processing_meta_key: str = Field(
        default="analysis:processing_meta", description="Ключ меты обработки"
    )
    processing_ttl_seconds: int = Field(default=600, ge=1, description="TTL аренды задачи (сек)")
    processing_recovery_priority: float = Field(
        default=0.0, description="Приоритет восстановления зависших задач"
    )
    queue_poll_seconds: float = Field(default=2.0, gt=0, description="Период опроса очереди (сек)")
    jobs_retry_key: str = Field(
        default="analysis:jobs_retries", description="Ключ счётчика ретраев"
    )

    # RAG-параметры
    chunk_max_chars: int = Field(default=1500, ge=200, description="Макс. размер чанка (символов)")
    top_k: int = Field(default=3, ge=1, description="Сколько чанков отдавать LLM на вопрос")
    tz_download_timeout: float = Field(
        default=30.0, gt=0, description="Таймаут скачивания ТЗ (сек)"
    )
    tz_verify_ssl: bool = Field(default=False, description="Проверять SSL при скачивании файла ТЗ")


class MarginServiceConfig(_BaseConfig):
    """Несекретная конфигурация margin_service (src/margin_service/config.yaml)."""

    parser_api_url: str = Field(default="http://localhost:8000", description="URL REST API парсера")
    parser_retry_backoff_seconds: float = Field(
        default=5.0, ge=0, description="Пауза при недоступности парсера (сек)"
    )
    redis_url: str = Field(default="redis://localhost:6379/0", description="URL Redis")
    jobs_key: str = Field(default="margin:jobs", description="Ключ очереди задач")
    results_key: str = Field(default="margin:results", description="Ключ результатов")
    processing_key: str = Field(default="margin:processing", description="Ключ обработки")
    processing_meta_key: str = Field(
        default="margin:processing_meta", description="Ключ меты обработки"
    )
    processing_ttl_seconds: int = Field(default=600, ge=1, description="TTL аренды задачи (сек)")
    processing_recovery_priority: float = Field(
        default=0.0, description="Приоритет восстановления зависших задач"
    )
    queue_poll_seconds: float = Field(default=2.0, gt=0, description="Период опроса очереди (сек)")
    jobs_retry_key: str = Field(default="margin:jobs_retries", description="Ключ счётчика ретраев")
    margin_rate: float = Field(
        default=1.0, ge=0, description="Норма прибыли: Margin = НМЦК × margin_rate"
    )
    score_round_digits: int = Field(default=2, ge=0, description="Округление score")


class PwinServiceConfig(_BaseConfig):
    """Несекретная конфигурация pwin_service (src/pwin_service/config.yaml)."""

    parser_api_url: str = Field(default="http://localhost:8000", description="URL REST API парсера")
    parser_retry_backoff_seconds: float = Field(
        default=5.0, ge=0, description="Пауза при недоступности парсера (сек)"
    )
    redis_url: str = Field(default="redis://localhost:6379/0", description="URL Redis")
    jobs_key: str = Field(default="pwin:jobs", description="Ключ очереди задач")
    results_key: str = Field(default="pwin:results", description="Ключ результатов")
    processing_key: str = Field(default="pwin:processing", description="Ключ обработки")
    processing_meta_key: str = Field(
        default="pwin:processing_meta", description="Ключ меты обработки"
    )
    processing_ttl_seconds: int = Field(default=600, ge=1, description="TTL аренды задачи (сек)")
    processing_recovery_priority: float = Field(
        default=0.0, description="Приоритет восстановления зависших задач"
    )
    queue_poll_seconds: float = Field(default=2.0, gt=0, description="Период опроса очереди (сек)")
    jobs_retry_key: str = Field(default="pwin:jobs_retries", description="Ключ счётчика ретраев")
    score_round_digits: int = Field(default=4, ge=0, description="Округление score")

    # Заглушка
    use_stub: bool = Field(default=False, description="Вернуть константное P(win) без расчёта")
    stub_pwin: float = Field(
        default=0.5, ge=0, le=1, description="Константа P(win) в режиме заглушки"
    )

    # Коэффициенты модели P(win)
    base_pwin: float = Field(default=0.4, ge=0, le=1, description="Базовая вероятность победы")
    k_smp: float = Field(default=1.5, ge=0, description="Закупка только для СМП")
    k_license_present: float = Field(default=3.0, ge=0, description="Лицензия ФСТЭК/ФСБ есть")
    k_license_absent: float = Field(default=0.1, ge=0, description="Лицензии ФСТЭК/ФСБ нет")
    k_large_threshold: float = Field(
        default=50_000_000.0, ge=0, description="Порог НМЦК «крупной», ₽"
    )
    k_large: float = Field(default=0.6, ge=0, description="Коэффициент крупной закупки")
    k_procedure_auction: float = Field(default=1.3, ge=0, description="Электронный аукцион")
    k_procedure_contest: float = Field(default=1.0, ge=0, description="Открытый конкурс")
    k_procedure_quotation: float = Field(default=0.8, ge=0, description="Запрос котировок")
    k_ai: float = Field(default=1.8, ge=0, description="Закупка ИИ-решений")
    ai_markers: list[str] = Field(
        default_factory=list,
        description="Маркеры ИИ-закупки в subject/okpd2 (регистронезависимый поиск подстроки)",
    )
    max_pwin_cap: float = Field(
        default=0.95, ge=0, le=1, description="Кап P(win) — защита от переоценки"
    )
