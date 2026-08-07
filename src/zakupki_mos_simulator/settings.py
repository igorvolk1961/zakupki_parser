"""Настройки имитатора (pydantic-settings + env)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_PKG_ROOT = Path(__file__).resolve().parents[0]


class Settings(BaseModel):
    """Общие настройки имитатора.

    Значения по умолчанию можно переопределить env-переменными с префиксом
    ``ZAKUPKI_SIM_`` (pydantic-settings).
    """

    host: str = "127.0.0.1"
    port: int = Field(default=8010, description="порт веб-имитатора (не 8000 — api парсера)")

    # LLM (OpenAI-совместимый /v1/chat/completions).
    llm_base_url: str = Field(default="https://api.openai.com/v1")
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3

    # Генерация.
    default_competencies_path: Path = _PKG_ROOT / "data" / "competencies.md"
    default_dataset_path: Path = _PKG_ROOT / "data" / "dataset.json"
    default_okpd2_tree: Path = _PKG_ROOT.parents[2] / "configs" / "codes" / "mos_okpd2_tree.json"
    default_okpd2_sections: list[str] = ["62", "63"]
    per_category: int = Field(default=8, description="закупок на категорию")
    temperature: float = 0.8

    # Демо-конфиги.
    demo_configs_path: Path = _PKG_ROOT / "demo_configs"


def load_settings(**overrides: Any) -> Settings:
    """Собирает настройки из env (ZAKUPKI_SIM_*) с переопределениями аргументами."""
    from pydantic_settings import BaseSettings

    class _Env(BaseSettings):
        model_config = {"env_prefix": "ZAKUPKI_SIM_", "extra": "ignore"}

        host: str | None = None
        port: int | None = None
        llm_base_url: str | None = None
        llm_api_key: str | None = None
        llm_model: str | None = None
        llm_timeout_seconds: float | None = None
        llm_max_retries: int | None = None
        temperature: float | None = None

    env = _Env()
    merged: dict[str, Any] = {}
    for key in ("host", "port", "llm_base_url", "llm_api_key", "llm_model"):
        val = getattr(env, key)
        if val is not None:
            merged[key] = val
    for key, val in overrides.items():
        if val is not None:
            merged[key] = val
    return Settings(**merged)
