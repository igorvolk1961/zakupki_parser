"""Сериализация извлечённых данных в JSON-безопасные значения (для JSONB)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def json_safe(data: Any) -> Any:
    """Рекурсивно приводит datetime к ISO-строке (для JSONB)."""
    if isinstance(data, dict):
        return {k: json_safe(v) for k, v in data.items()}
    if isinstance(data, list):
        return [json_safe(v) for v in data]
    if isinstance(data, datetime):
        return data.isoformat()
    return data
