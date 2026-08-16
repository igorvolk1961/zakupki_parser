"""Модуль расчёта маржи (Margin).

Первый этап: ``Margin = НМЦК × margin_rate`` (как в дефолтном подходе парсера).
"""

from __future__ import annotations

from typing import Any


def compute_margin(record: dict[str, Any], margin_rate: float) -> float:
    """Маржа (первый этап): НМЦК × margin_rate."""
    try:
        nmck = float(record.get("nmck") or 0.0)
    except (TypeError, ValueError):
        nmck = 0.0
    return round(nmck * margin_rate, 2)
