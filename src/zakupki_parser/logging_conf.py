"""Настройка логирования по конфигу ``config_log.yaml``.

Реализация вынесена в общий модуль ``scoring_common.logging``, чтобы парсер и
фоновые сервисы каскада скоринга управляли логами единообразно.
"""

from __future__ import annotations

from scoring_common.logging import (
    _NameRewriteFilter,
    _ScrubbingFormatter,
)
from scoring_common.logging import (
    setup_logging as _setup_logging,
)
from zakupki_parser.config.models import LoggingConfig

__all__ = ["setup_logging", "_NameRewriteFilter", "_ScrubbingFormatter"]


def setup_logging(cfg: LoggingConfig) -> None:
    """Конфигурирует корневой логгер согласно ``cfg``."""
    _setup_logging(cfg)
