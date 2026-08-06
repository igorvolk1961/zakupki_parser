"""Репозиторий закупок: запись с контролем дубликатов и чтение."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from zakupki_parser.storage.customers import normalize_name
from zakupki_parser.storage.db import Customer, Database, Procurement

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
        stmt = (
            select(Procurement)
            .where(Procurement.id == procurement_id)
            .options(selectinload(Procurement.customer_rel))
        )
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
            conditions.append(Customer.name.ilike(f"%{customer}%"))

        stmt = select(Procurement).options(selectinload(Procurement.customer_rel))
        if customer:
            stmt = stmt.join(Customer, Procurement.customer_id == Customer.id)
        stmt = stmt.where(*conditions).order_by(Procurement.id.desc())
        count_stmt = select(func.count(Procurement.id)).where(*conditions)
        if customer:
            count_stmt = count_stmt.select_from(Procurement).join(
                Customer, Procurement.customer_id == Customer.id
            )

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
            record.customer_id = await self._resolve_customer_id(
                session, data.get("customer"), data.get("inn")
            )
            session.add(record)
            await session.commit()
        logger.info("Сохранена заявка %s (%s)", number, source_platform)
        return True

    async def _resolve_customer_id(
        self, session: AsyncSession, name: str | None, inn: str | None
    ) -> int | None:
        """Резолвит заказчика (ADR-4): find-or-create по нормализованному имени/ИНН.

        Возвращает ``customers.id`` или None (нет имени заказчика). Конкурентные
        вставки одного заказчика снимаются ``ON CONFLICT (normalized_name) DO NOTHING``
        с последующим повторным SELECT.
        """
        normalized = normalize_name(name)
        if not normalized:
            return None

        cust = (
            await session.execute(select(Customer).where(Customer.normalized_name == normalized))
        ).scalar_one_or_none()
        if cust is not None:
            if inn and not cust.inn:
                cust.inn = inn
                await session.flush()
            return cust.id

        if inn:
            cust = (
                await session.execute(select(Customer).where(Customer.inn == inn))
            ).scalar_one_or_none()
            if cust is not None:
                return cust.id

        stmt = (
            pg_insert(Customer)
            .values(name=name or normalized, normalized_name=normalized, inn=inn)
            .on_conflict_do_nothing(index_elements=["normalized_name"])
            .returning(Customer.id)
        )
        cid = (await session.execute(stmt)).scalar_one_or_none()
        if cid is not None:
            return cid
        # Конфликт: другой процесс уже создал заказчика — берём существующего.
        cust = (
            await session.execute(select(Customer).where(Customer.normalized_name == normalized))
        ).scalar_one()
        return cust.id

    async def list_for_scoring(
        self, method: str, *, limit: int = 50, offset: int = 0
    ) -> list[Procurement]:
        """Записи, ожидающие внешнего скоринга (score_method == method)."""
        stmt = (
            select(Procurement)
            .where(Procurement.score_method == method)
            .options(selectinload(Procurement.customer_rel))
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

    async def get_customer(self, customer_id: int) -> Customer | None:
        async with self._db.session() as session:
            return await session.get(Customer, customer_id)

    async def list_customers(
        self,
        *,
        name: str | None = None,
        inn: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Customer], int]:
        """Справочник заказчиков с фильтрами и общим количеством."""
        conditions: list[ColumnElement[bool]] = []
        if name:
            conditions.append(Customer.name.ilike(f"%{name}%"))
        if inn:
            conditions.append(Customer.inn == inn)

        stmt = select(Customer).where(*conditions).order_by(Customer.id.asc())
        count_stmt = select(func.count(Customer.id)).where(*conditions)
        async with self._db.session() as session:
            result = await session.execute(stmt.limit(limit).offset(offset))
            rows = list(result.scalars().all())
            total = (await session.execute(count_stmt)).scalar_one()
        return rows, total

    async def set_customer_rating(self, customer_id: int, rating: float) -> bool:
        """Устанавливает рейтинг заказчика (вызывается внешним сервисом).

        Возвращает True, если заказчик найден и рейтинг обновлён.
        """
        async with self._db.session() as session:
            obj = await session.get(Customer, customer_id)
            if obj is None:
                return False
            obj.rating = rating
            await session.commit()
            logger.info("Обновлён рейтинг заказчика %s: %s", customer_id, rating)
            return True
