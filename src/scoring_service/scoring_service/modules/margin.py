"""Заглушка Margin — маржа.

На первом этапе повторяет дефолтный подход парсера: Margin = НМЦК закупки
(``scoring.compute_default_score`` использует НМЦК как маржу). Опционально
применяется норма прибыли ``margin_rate`` из настроек.
"""

from __future__ import annotations

from typing import Any

from scoring_service.settings import Settings


def margin(record: dict[str, Any], settings: Settings) -> float:
    """Маржа (заглушка): НМЦК × margin_rate."""
    nmck = float(record.get("nmck") or 0.0)
    return round(nmck * settings.margin_rate, 2)
