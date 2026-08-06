"""Заглушка P(win) — вероятность победы.

На первом этапе повторяет дефолтный подход парсера: P(win) = константа из конфига
(по умолчанию 1.0). В будущем — из рейтинга заказчика (ADR-4/ADR-6).
"""

from __future__ import annotations

from typing import Any

from scoring_service.settings import Settings


def p_win(record: dict[str, Any], settings: Settings) -> float:
    """Вероятность победы (заглушка: константа из настроек)."""
    return settings.p_win
