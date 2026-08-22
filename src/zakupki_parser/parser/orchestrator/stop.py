"""Условия прекращения обработки закупки (stop-условия) — сроки.

Миксин, используемый классом ``Orchestrator``. Флаг задаётся в
``config_service.yaml -> search_criteria.deadline_not_expired``.

Ключевые слова и слова-исключения здесь НЕ обрабатываются: по R9 они применяются
обязательной клиентской пост-фильтрацией ДО записи в БД (см. ``parser.filtering``)
с использованием стандартного синтаксиса ``слов*``/``(…)~N`` из файла профиля.
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
        """Проверяет stop-условия по срокам (deadline).

        Возвращает True, если закупку следует ПРОПУСТИТЬ (обработка прекращается).
        """
        sc = self._cfg.service.search_criteria
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
        return False
