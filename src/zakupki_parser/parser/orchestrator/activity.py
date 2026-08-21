"""Определение активности закупки по статусу.

Миксин, используемый классом ``Orchestrator``. Нормализация статусов нужна,
чтобы статусы площадок («ПРИЕМ ПРЕДЛОЖЕНИЙ ...», «Прием предложений»)
корректно сопоставлялись с ``list_config.active_statuses``.

Срок актуальности (deadline) здесь НЕ учитывается: ``is_active`` в БД отражает
только статус. Проверка текущей даты выполняется на стороне клиента при чтении
и фильтрации (см. ``ProcurementRepository.list_procurements`` и API).
"""

from __future__ import annotations

import re
from typing import Any

from zakupki_parser.config.models import PlatformDom


class ActivityMixin:
    """Активность закупки: ``_is_active`` + нормализация статуса."""

    # Задаётся в ``Orchestrator.__init__``.
    _platform: PlatformDom

    def _active_status_set(self) -> set[str]:
        """Нормализованные активные статусы площадки (кеш на проход)."""
        cached = getattr(self, "_normalized_active_statuses", None)
        if cached is None:
            statuses = self._platform.list_config.active_statuses or []
            cached = {self._normalize_status(s) for s in statuses}
            self._normalized_active_statuses = cached
        return cached

    def _is_active(self, record: dict[str, Any]) -> bool:
        """Определяет активность закупки по статусу.

        Закупка НЕ активна, если явно задан неактивный статус: в конфиге
        площадки задан ``list_config.active_statuses`` и status закупки не входит
        в список.

        Если ``active_statuses`` не задан — по статусу не фильтруем (любой статус
        считается активным).
        """
        if self._platform.list_config.active_statuses:
            status = (record.get("status") or "").strip()
            if self._normalize_status(status) not in self._active_status_set():
                return False
        return True

    @staticmethod
    def _normalize_status(status: str) -> str:
        """Нормализует статус для сопоставления: нижний регистр, без ``...``/``…``."""
        return re.sub(r"[.…\s]+$", "", status.strip().lower())
