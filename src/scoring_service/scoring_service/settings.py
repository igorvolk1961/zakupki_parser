"""Настройки сервиса скоринга.

Всё через env-переменные с префиксом ``SCORE_`` (pydantic-settings).
LangFuse — стандартные переменные ``LANGFUSE_*``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация сервиса скоринга закупок."""

    model_config = SettingsConfigDict(env_prefix="SCORE_", env_file=".env", extra="ignore")

    # LLM (OpenAI-совместимый)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = "sk-dummy"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0

    # Парсер закупок (REST, без БД)
    parser_api_url: str = "http://localhost:8000"

    # Redis-очередь
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "scoring:jobs"
    results_key: str = "scoring:results"
    processing_key: str = "scoring:processing"
    processing_meta_key: str = "scoring:processing_meta"
    processing_ttl_seconds: int = 600
    processing_recovery_priority: float = 0.0
    queue_poll_seconds: float = 2.0

    # Компетенции поставщика
    competencies_file: Path = Path("data/competencies.md")

    # Стубы P(win)/Margin (дефолтный подход парсера)
    p_win: float = 1.0
    margin_rate: float = 1.0

    # Пайплайн
    num_refine_rounds: int = 1
    max_fit_score: float = 10.0
    min_fit_score: float = 0.0
    score_round_digits: int = 2
    normalize_fit_for_score: bool = True

    # LangFuse (None = выключен)
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    # Опциональная авторизация HTTP-эндпоинтов (None = выключено, dev)
    auth_token: str | None = None

    def competencies(self) -> str:
        """Текст компетенций поставщика из файла."""
        return self.competencies_file.read_text(encoding="utf-8")


def get_settings() -> Settings:
    return Settings()
