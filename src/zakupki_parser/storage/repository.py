"""Репозиторий закупок: запись с контролем дубликатов и чтение."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from zakupki_parser.storage.db import Database, Procurement

logger = logging.getLogger(__name__)


def _round_score(value: Any) -> float | None:
    """Округляет score до копеек (0.01 ₽) перед записью в БД."""
    if value is None:
        return None
    return round(float(value), 2)


class ProcurementRepository:
    """Операции с таблицей ``procurements``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def last_processed_date(
        self, platform_id: str, now: datetime, default_cutoff_days: int
    ) -> datetime:
        """Дата последней обработанной записи площадки (MAX(update_date)).

        Если для площадки ещё нет ни одной записи — ``now - default_cutoff_days``.
        """
        stmt = select(func.max(Procurement.update_date)).where(
            Procurement.source_platform == platform_id
        )
        async with self._db.session() as session:
            max_date = (await session.execute(stmt)).scalar_one_or_none()
        if max_date is None:
            return now - timedelta(days=default_cutoff_days)
        return max_date

    async def get_by_id(self, procurement_id: int) -> Procurement | None:
        stmt = select(Procurement).where(Procurement.id == procurement_id)
        async with self._db.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_procurements(
        self,
        *,
        number: str | None = None,
        source_platform: str | None = None,
        okpd2: str | None = None,
        customer: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Procurement], int]:
        """Возвращает записи и их общее количество по фильтрам."""
        conditions: list[ColumnElement[bool]] = []
        if number:
            conditions.append(Procurement.number.ilike(f"%{number}%"))
        if source_platform:
            conditions.append(Procurement.source_platform == source_platform)
        if okpd2:
            conditions.append(Procurement.okpd2_codes.ilike(f"%{okpd2}%"))
        if customer:
            conditions.append(Procurement.customer.ilike(f"%{customer}%"))

        stmt = select(Procurement).where(*conditions).order_by(Procurement.id.desc())
        count_stmt = select(func.count(Procurement.id)).where(*conditions)

        async with self._db.session() as session:
            result = await session.execute(stmt.limit(limit).offset(offset))
            rows = list(result.scalars().all())
            total = (await session.execute(count_stmt)).scalar_one()
        return rows, total

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
            publication_date=data.get("publication_date"),
            update_date=data.get("update_date"),
            deadline=data.get("deadline"),
            execution_term=data.get("execution_term"),
            security_amount=data.get("security_amount"),
            security_amount_unit=data.get("security_amount_unit"),
            advance=data.get("advance"),
            okpd2_codes=data.get("okpd2_codes") or data.get("okpd2_code"),
            kpgz_codes=data.get("kpgz_codes") or data.get("kpgz_code"),
            technical_spec_url=data.get("technical_spec_url"),
            technical_spec_name=data.get("technical_spec_name"),
            files_json=data.get("files_json"),
            score=_round_score(data.get("score")),
            score_method=data.get("score_method"),
            detail_json=data.get("detail_json"),
        )
        async with self._db.session() as session:
            session.add(record)
            await session.commit()
        logger.info("Сохранена заявка %s (%s)", number, source_platform)
        return True

    async def list_for_scoring(
        self, method: str, *, limit: int = 50, offset: int = 0
    ) -> list[Procurement]:
        """Записи, ожидающие внешнего скоринга (score_method == method)."""
        stmt = (
            select(Procurement)
            .where(Procurement.score_method == method)
            .order_by(Procurement.id.asc())
            .limit(limit)
            .offset(offset)
        )
        async with self._db.session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def set_score_method(self, procurement_id: int, method: str) -> None:
        async with self._db.session() as session:
            obj = await session.get(Procurement, procurement_id)
            if obj is not None:
                obj.score_method = method
                await session.commit()

    async def update_score(self, procurement_id: int, score: float, method: str) -> None:
        async with self._db.session() as session:
            obj = await session.get(Procurement, procurement_id)
            if obj is not None:
                rounded = _round_score(score)
                obj.score = rounded
                obj.score_method = method
                await session.commit()
                logger.info(
                    "Обновлён score заявки %s: %s (метод %s)",
                    procurement_id,
                    rounded,
                    method,
                )

    async def update_technical_spec(
        self,
        procurement_id: int,
        *,
        name: str | None = None,
        url: str | None = None,
    ) -> None:
        """Обновляет метаданные ТЗ (вызывается внешним сервисом обработки файлов)."""
        async with self._db.session() as session:
            obj = await session.get(Procurement, procurement_id)
            if obj is not None:
                if name is not None:
                    obj.technical_spec_name = name
                if url is not None:
                    obj.technical_spec_url = url
                await session.commit()
                logger.info("Обновлены метаданные ТЗ заявки %s", procurement_id)
