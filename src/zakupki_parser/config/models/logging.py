"""Модель конфигурации логирования (config_log.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    """Конфигурация логирования."""

    level: str = Field(default="INFO")
    format: str = Field(default="%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    file: str | None = Field(default=None, description="путь к файлу лога")
    file_level: str = Field(default="DEBUG")
    console: bool = Field(default=True)
    truncate_on_start: bool = Field(
        default=False,
        description="очищать файл лога при старте сервиса (True) или дописывать (False)",
    )
