"""Условия прекращения обработки закупки (stop-условия).

Миксин, используемый классом ``Orchestrator``. Набор флагов задаётся в
``config_service.yaml -> stop_conditions``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from zakupki_parser.config.models import AppConfig

logger = logging.getLogger(__name__)


class StopMixin:
    """Проверка условий прекращения обработки закупки."""

    # Задаётся в ``Orchestrator.__init__``.
    _now: datetime
    _cfg: AppConfig

    def _check_stop_conditions(self, record: dict[str, Any]) -> bool:
        """Проверяет набор флагов прекращения обработки закупки.

        Возвращает True, если закупку следует ПРОПУСТИТЬ (обработка прекращается).
        """
        sc = self._cfg.service.stop_conditions
        if sc.deadline_not_expired:
            deadline = record.get("deadline")
            if not isinstance(deadline, datetime):
                return False
            if deadline < self._now:
                logger.info(
                    "Закупка %s пропущена: срок приёма истёк (%s)",
                    record.get("number"),
                    deadline,
                )
                return True
            if sc.min_deadline_days is not None:
                days_left = (deadline - self._now).total_seconds() / 86400
                if days_left < sc.min_deadline_days:
                    logger.info(
                        "Закупка %s пропущена: до срока подачи %.1f дн. < %d",
                        record.get("number"),
                        days_left,
                        sc.min_deadline_days,
                    )
                    return True
        return False
