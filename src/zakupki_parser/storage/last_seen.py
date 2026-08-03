"""Хранение даты последней обработанной записи.

Не хранится в БД (может быть получена SQL-запросом); сохраняется в state-файле
в каталоге данных (``data/last_seen.json``). По умолчанию порог берётся из
``config_service.yaml -> default_cutoff_days``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LastSeenStore:
    """Чтение/запись даты последней обработки по площадке."""

    def __init__(self, data_dir: Path, default_cutoff_days: int) -> None:
        self._path = data_dir / "last_seen.json"
        self._default_cutoff_days = default_cutoff_days

    def load(self, platform_id: str, now: datetime) -> datetime:
        """Возвращает дату последней обработки; если её нет — ``now - cutoff``."""
        data: dict[str, Any] = {}
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Не удалось прочитать last_seen.json, используем дефолт")
                data = {}
        raw = data.get(platform_id)
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                logger.warning("Некорректная дата для %s", platform_id)
        return now - timedelta(days=self._default_cutoff_days)

    def save(self, platform_id: str, value: datetime) -> None:
        """Сохраняет дату последней обработки для площадки."""
        data: dict[str, Any] = {}
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        data[platform_id] = value.isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
