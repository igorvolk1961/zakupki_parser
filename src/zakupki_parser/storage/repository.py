"""Репозиторий закупок: запись с контролем дубликатов и чтение."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from zakupki_parser.config.models import SCORE_METHOD_FIT, SCORE_METHOD_STAGES
from zakupki_parser.storage.customers import normalize_name
from zakupki_parser.storage.db import Customer, Database, Procurement

logger = logging.getLogger(__name__)


def _round_score(value: Any) -> float | None:
    """Округляет score до копеек (0.01 ₽) перед записью в БД."""
    if value is None:
        return None
    return round(float(value), 2)


def effective_is_active(
    is_active: bool, deadline: datetime | None, now: datetime | None = None
) -> bool:
    """Эффективная активность на стороне клиента.

    Активна, если закупка активна по статусу (``is_active``) И срок актуальности
    не истёк (``deadline`` отсутствует или не раньше ``now``).
    """
    if not is_active:
        return False
    if deadline is None:
        return True
    return deadline >= (now or datetime.now(UTC))


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
        active: bool | None = None,
        min_fit_score: float | None = None,
        limit: int = 20,
        offset: int = 0,
        now: datetime | None = None,
    ) -> tuple[list[Procurement], int]:
        """Возвращает записи и их общее количество по фильтрам.

        ``active`` учитывает текущую дату на стороне клиента: закупка активна,
        если активна по статусу (is_active) И срок актуальности не истёк
        (deadline отсутствует или не раньше ``now``).
        """
        conditions: list[ColumnElement[bool]] = []
        if number:
            conditions.append(Procurement.number.ilike(f"%{number}%"))
        if source_platform:
            conditions.append(Procurement.source_platform == source_platform)
        if okpd2:
            conditions.append(Procurement.okpd2_codes.ilike(f"%{okpd2}%"))
        if customer:
            conditions.append(Customer.name.ilike(f"%{customer}%"))
        if active is not None:
            now = now or datetime.now(UTC)
            if active:
                conditions.append(
                    and_(
                        Procurement.is_active.is_(True),
                        or_(
                            Procurement.deadline.is_(None),
                            Procurement.deadline >= now,
                        ),
                    )
                )
            else:
                conditions.append(
                    or_(
                        Procurement.is_active.is_(False),
                        and_(
                            Procurement.deadline.is_not(None),
                            Procurement.deadline < now,
                        ),
                    )
                )
        if min_fit_score is not None:
            conditions.append(Procurement.fit_score >= min_fit_score)
            # Учитываем только скор, полученный стадиями внешнего каскада скоринга
            # (fit/pwin/margin): дефолтный (эвристика до обработки) и deadline_expired
            # не являются «релевантными».
            conditions.append(Procurement.score_method.in_(SCORE_METHOD_STAGES))

        stmt = select(Procurement).options(selectinload(Procurement.customer_rel))
        if customer:
            stmt = stmt.join(Customer, Procurement.customer_id == Customer.id)
        stmt = stmt.where(*conditions).order_by(Procurement.id.asc())
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

    async def known_numbers(self, platform_id: str) -> set[str]:
        """Все номера закупок площадки — для пропуска повторной обработки.

        Используется оркестратором, чтобы не открывать детальные страницы уже
        сохранённых закупок при повторных проходах (relevance-режим).
        """
        stmt = select(Procurement.number).where(Procurement.source_platform == platform_id)
        async with self._db.session() as session:
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}

    async def count(self, platform_id: str | None = None) -> int:
        """Число закупок (всей площадки или указанной platform_id).

        Используется для раннего сравнения с числом результатов поиска: если в БД
        записей не меньше, чем нашёл поиск, новые закупки, скорее всего, отсутствуют.
        """
        stmt = select(func.count(Procurement.id))
        if platform_id is not None:
            stmt = stmt.where(Procurement.source_platform == platform_id)
        async with self._db.session() as session:
            return int((await session.execute(stmt)).scalar_one())

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
            files_json=data.get("files_json"),
            score=_round_score(data.get("score")),
            fit_score=_round_score(data.get("fit_score")),
            p_win=_round_score(data.get("p_win")),
            margin=_round_score(data.get("margin")),
            score_method=data.get("score_method"),
            is_active=bool(data.get("is_active", True)),
            detail_json=data.get("detail_json"),
        )
        async with self._db.session() as session:
            record.customer_id = await self._resolve_customer_id(
                session, data.get("customer"), data.get("inn")
            )
            session.add(record)
            await session.commit()
        # Отдаём id записи в исходный dict — нужен для постановки задания на внешний
        # скоринг (POST /api/scoring/jobs, ADR-7).
        data["id"] = record.id
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

    async def update_score(
        self,
        procurement_id: int,
        score: float,
        fit_score: float | None = None,
        method: str = SCORE_METHOD_FIT,
        embedding_similarity: float | None = None,
        p_win: float | None = None,
        margin: float | None = None,
    ) -> None:
        async with self._db.session() as session:
            obj = await session.get(Procurement, procurement_id)
            if obj is not None:
                rounded = _round_score(score)
                obj.score = rounded
                obj.fit_score = _round_score(fit_score) if fit_score is not None else None
                obj.p_win = _round_score(p_win) if p_win is not None else None
                obj.margin = _round_score(margin) if margin is not None else None
                obj.score_method = method
                obj.embedding_similarity = (
                    round(float(embedding_similarity), 4)
                    if embedding_similarity is not None
                    else None
                )
                await session.commit()
                logger.info(
                    "Обновлён score заявки %s: %s (fit %s, p_win %s, margin %s, метод %s)",
                    procurement_id,
                    rounded,
                    obj.fit_score,
                    obj.p_win,
                    obj.margin,
                    method,
                )

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

    async def clear_all(self) -> dict[str, int]:
        """Полностью очищает БД (закупки и заказчики). Возвращает число удалённых."""
        async with self._db.session() as session:
            procs = (await session.execute(select(func.count(Procurement.id)))).scalar_one()
            await session.execute(delete(Procurement))
            cust = (await session.execute(select(func.count(Customer.id)))).scalar_one()
            await session.execute(delete(Customer))
            await session.commit()
        logger.info("БД очищена: %s закупок, %s заказчиков", procs, cust)
        return {"procurements": int(procs), "customers": int(cust)}

    async def delete_inactive(self, now: datetime | None = None) -> int:
        """Удаляет неактивные закупки (is_active=false или истёкший срок актуальности).

        Клиентская операция: активность учитывает текущую дату, как в фильтре
        ``active`` в ``list_procurements``. Заказчики не затрагиваются.
        """
        now = now or datetime.now(UTC)
        stmt = delete(Procurement).where(
            or_(
                Procurement.is_active.is_(False),
                and_(
                    Procurement.deadline.is_not(None),
                    Procurement.deadline < now,
                ),
            )
        )
        async with self._db.session() as session:
            result = cast("CursorResult[Any]", await session.execute(stmt))
            await session.commit()
        deleted = int(result.rowcount or 0)
        logger.info("Удалено неактивных закупок: %s", deleted)
        return deleted

    async def delete_irrelevant(self, min_fit_score: float) -> int:
        """Удаляет нерелевантные закупки среди обработанных внешним каскадом скоринга.

        Учитываются ТОЛЬКО записи, прошедшие внешний скоринг (score_method — одна
        из стадий каскада fit/pwin/margin): релевантна закупка с fit_score >= порога,
        нерелевантна — с fit_score < порога (или NULL). Записи без внешнего скоринга
        (default/deadline_expired) не затрагиваются. Заказчики не затрагиваются.
        """
        stmt = delete(Procurement).where(
            Procurement.score_method.in_(SCORE_METHOD_STAGES),
            or_(
                Procurement.fit_score.is_(None),
                Procurement.fit_score < min_fit_score,
            ),
        )
        async with self._db.session() as session:
            result = cast("CursorResult[Any]", await session.execute(stmt))
            await session.commit()
        deleted = int(result.rowcount or 0)
        logger.info("Удалено нерелевантных закупок (fit_score < %s): %s", min_fit_score, deleted)
        return deleted
