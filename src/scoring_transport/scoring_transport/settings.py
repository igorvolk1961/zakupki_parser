"""Настройки транспорта скоринга.

Всё через env-переменные с префиксом ``TRANSPORT_`` (pydantic-settings).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация транспорта скоринга."""

    model_config = SettingsConfigDict(env_prefix="TRANSPORT_", env_file=".env", extra="ignore")

    # Redis-очередь (та же, что у scoring_service)
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "scoring:jobs"
    results_key: str = "scoring:results"
    result_timeout_seconds: float = 5.0

    # Парсер закупок (REST)
    parser_api_url: str = "http://localhost:8000"

    # Приоритет по умолчанию, если score карточки не определён
    priority_default: float = 0.0

    # Fit-таблица (зеркало config_score.yaml) для пересчёта приоритета
    fit_table: dict[str, float] = {
        "62.01": 0.9,
        "62.02": 0.8,
        "62.09": 0.7,
        "63.11": 0.6,
    }
    default_fit: float = 0.5
    p_win: float = 1.0

    # Ретраи возврата результата в парсер
    retry_max: int = 5
    retry_backoff_seconds: float = 2.0

    # Опциональная авторизация HTTP-эндпоинтов (None = выключено, dev)
    auth_token: str | None = None


def get_settings() -> Settings:
    return Settings()
