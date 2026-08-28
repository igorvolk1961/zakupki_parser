"""Совместимый импорт Giga-клиента.

Реализация переехала в ``scoring_common.giga`` (общий модуль для scoring_service и
analysis_service). Модуль сохранён для обратной совместимости импортов:
    - ``scoring_service.modules.giga_embedder.GigaTokenProvider``
    - ``scoring_service.modules.giga_embedder.GigaEmbedder``
    - ``scoring_service.modules.giga_embedder.GigaTokenError``
"""

from __future__ import annotations

from scoring_common.giga import (
    GigaEmbedder,
    GigaTokenError,
    GigaTokenProvider,
)

__all__ = ["GigaEmbedder", "GigaTokenError", "GigaTokenProvider"]
