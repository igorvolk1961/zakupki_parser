"""Сохранение закупки в БД с вежливой деградацией.

Миксин, используемый классом ``Orchestrator``. Circuit breaker учитывает ТОЛЬКО
транзиентные ошибки доступности БД; ошибки данных/схемы не открывают CB.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.exc import IntegrityError

from zakupki_parser.circuit import CircuitBreaker
from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.orchestrator.state import OrchestratorState
from zakupki_parser.storage.db_errors import is_data_db_error, is_transient_db_error
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class PersistenceMixin(OrchestratorState):
    """Запись закупки в БД (``_persist``)."""

    # Задаётся в ``Orchestrator.__init__``.
    _cfg: AppConfig
    _repository: ProcurementRepository | None
    _db_cb: CircuitBreaker
    _on_record_saved: Callable[[], Awaitable[None]] | None

    async def _persist(self, record: dict[str, Any]) -> bool:
        """Сохраняет закупку в БД с вежливой деградацией.

        Circuit breaker учитывает ТОЛЬКО транзиентные ошибки доступности БД;
        ошибки данных/схемы (например, усечение значения) не открывают CB.
        Транзиентные ошибки повторяются с экспоненциальным backoff
        (base × 2^(n-1)) до исчерпания попыток.
        """
        if not self._cfg.ops.db.enabled or self._repository is None:
            return False
        if not self._db_cb.allow_request():
            logger.warning("БД недоступна (circuit open), запись пропущена")
            return False

        db_cfg = self._cfg.ops.db
        attempts = db_cfg.retry_max_attempts
        for attempt in range(1, attempts + 1):
            try:
                saved = await self._repository.upsert(record)
                self._db_cb.record_success()
                if saved and self._on_record_saved is not None:
                    await self._on_record_saved()
                return saved
            except IntegrityError as exc:
                # Конкурентная вставка того же номера — не ошибка доступности.
                # БД доступна, поэтому сбрасываем счётчик отказов CB.
                logger.info("Дубликат по unique-констрейнту: %s", exc)
                self._db_cb.record_success()
                return False
            except Exception as exc:  # noqa: BLE001
                if is_data_db_error(exc):
                    logger.error("Ошибка данных при записи закупки: %s", exc)
                    return False
                if is_transient_db_error(exc) and attempt < attempts:
                    delay = db_cfg.retry_backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "Транзиентная ошибка БД (%s), retry %d/%d через %.1f с",
                        exc,
                        attempt,
                        attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                self._db_cb.record_failure()
                logger.error("Ошибка записи в БД: %s", exc)
                return False
        return False
