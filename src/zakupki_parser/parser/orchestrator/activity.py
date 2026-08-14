"""Определение активности закупки (статус + срок актуальности).

Миксин, используемый классом ``Orchestrator``. Нормализация статусов нужна,
чтобы статусы площадок («ПРИЕМ ПРЕДЛОЖЕНИЙ ...», «Прием предложений»)
корректно сопоставлялись с ``list_config.active_statuses``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from zakupki_parser.config.models import PlatformDom


class ActivityMixin:
    """Активность закупки: ``_is_active`` + нормализация статуса."""

    # Задаётся в ``Orchestrator.__init__``.
    _now: datetime
    _platform: PlatformDom

    def _is_active(self, record: dict[str, Any]) -> bool:
        """Определяет активность закупки.

        Закупка НЕ активна, если:
          - явно задан неактивный статус: в конфиге площадки задан
            ``list_config.active_statuses`` и status закупки не входит в список;
          - истёк срок актуальности: переменная ``deadline`` — datetime и раньше
            текущего момента.

        Если ``active_statuses`` не задан — по статусу не фильтруем (любой статус
        считается активным), но проверка срока актуальности остаётся.
        """
        statuses = self._platform.list_config.active_statuses
        if statuses:
            status = (record.get("status") or "").strip()
            if self._normalize_status(status) not in {self._normalize_status(s) for s in statuses}:
                return False
        deadline = record.get("deadline")
        return not (isinstance(deadline, datetime) and deadline < self._now)

    @staticmethod
    def _normalize_status(status: str) -> str:
        """Нормализует статус для сопоставления: нижний регистр, без ``...``/``…``."""
        return re.sub(r"[.…\s]+$", "", status.strip().lower())
