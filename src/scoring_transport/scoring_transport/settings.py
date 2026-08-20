"""Настройки транспорта скоринга.

Всё через env-переменные с префиксом ``TRANSPORT_`` (pydantic-settings).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация транспорта скоринга."""

    model_config = SettingsConfigDict(env_prefix="TRANSPORT_", env_file=".env", extra="ignore")

    # Redis-очередь (та же, что у scoring_service/pwin/margin сервисов)
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "scoring:jobs"
    results_key: str = "scoring:results"
    # Очереди каскада скоринга (стадии P(win)/Margin).
    pwin_jobs_key: str = "pwin:jobs"
    pwin_results_key: str = "pwin:results"
    margin_jobs_key: str = "margin:jobs"
    margin_results_key: str = "margin:results"
    analysis_jobs_key: str = "analysis:jobs"
    analysis_results_key: str = "analysis:results"
    result_timeout_seconds: float = 5.0

    # Парсер закупок (REST)
    parser_api_url: str = "http://localhost:8000"
    # Внутренний токен парсера для служебных эндпоинтов (POST /score):
    # передаётся заголовком X-Internal-Token. Из env TRANSPORT_PARSER_INTERNAL_TOKEN.
    parser_internal_token: str | None = None

    # Приоритет по умолчанию, если в задаче не передан (обычно приходит из парсера)
    priority_default: float = 0.0

    # Ретраи возврата результата в парсер
    retry_max: int = 5
    retry_backoff_seconds: float = 2.0

    # Опциональная авторизация HTTP-эндпоинтов (None = выключено, dev)
    auth_token: str | None = None


def get_settings() -> Settings:
    return Settings()
