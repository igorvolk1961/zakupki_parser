"""Репозиторий закупок: запись с контролем дубликатов."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from zakupki_parser.storage.db import Database, Procurement

logger = logging.getLogger(__name__)


class ProcurementRepository:
    """Операции с таблицей ``procurements``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def exists(self, number: str, source_platform: str) -> bool:
        """Проверяет наличие заявки с указанным номером на площадке."""
        stmt = select(Procurement.id).where(
            Procurement.number == number,
            Procurement.source_platform == source_platform,
        )
        async with self._db.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def upsert(self, data: dict[str, Any]) -> bool:
        """Записывает заявку.

        Возвращает True, если запись была добавлена; False, если такая заявка
        (number + source_platform) уже существует (повторная запись исключена).

        Реализация: сначала явная проверка существования, затем INSERT. Второй
        уровень защиты — уникальный констрейнт в БД (``uq_procurement_number_platform``).
        """
        number = data.get("number")
        source_platform = data.get("source_platform")
        if not number or not source_platform:
            logger.warning("Пропуск записи: нет number/source_platform")
            return False

        if await self.exists(number, source_platform):
            logger.info("Дубликат: заявка %s (%s) уже сохранена", number, source_platform)
            return False

        record = Procurement(
            number=str(number),
            source_platform=source_platform,
            url=data.get("url"),
            customer=data.get("customer"),
            law=data.get("law"),
            subject=data.get("subject"),
            nmck=data.get("nmck"),
            deadline=data.get("deadline"),
            execution_term=data.get("execution_term"),
            okpd2_codes=data.get("okpd2_codes"),
            kpgz_codes=data.get("kpgz_codes"),
            detail_json=data.get("detail_json"),
        )
        async with self._db.session() as session:
            session.add(record)
            await session.commit()
        logger.info("Сохранена заявка %s (%s)", number, source_platform)
        return True
