"""Базовый класс эксплуатационной конфигурации."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _BaseConfig(BaseModel):
    """Базовый класс конфигурации: неизвестные ключи — ошибка (reject опечаток)."""

    model_config = ConfigDict(extra="forbid")
