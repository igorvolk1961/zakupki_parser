"""Модель подключения к БД (config_ops.yaml -> db)."""

from __future__ import annotations

from pydantic import Field

from zakupki_parser.config.models.ops.base import _BaseConfig


class DbConfig(_BaseConfig):
    """Параметры подключения к базе данных."""

    dsn: str = Field(default="postgresql://postgres:postgres@localhost:5432/zakupki")
    enabled: bool = Field(default=True)
    connect_timeout_seconds: float = Field(default=5.0, ge=0)
    pool_min: int = Field(default=1, ge=0)
    pool_max: int = Field(default=5, ge=1)
    retry_max_attempts: int = Field(
        default=3, ge=1, description="повторы записи при транзиентной ошибке БД"
    )
    retry_backoff_seconds: float = Field(
        default=1.0, ge=0, description="базовая пауза между повторами (растёт линейно)"
    )
