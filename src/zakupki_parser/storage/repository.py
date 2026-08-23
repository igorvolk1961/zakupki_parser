"""Репозиторий закупок: запись с контролем дубликатов и чтение."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from zakupki_parser.config.models import SCORE_METHOD_STAGES
from zakupki_parser.storage.customers import normalize_name
from zakupki_parser.storage.db import (
    Customer,
    Database,
    Keyword,
    ProcedureType,
    ProcedureTypeMapping,
    Procurement,
    ProcurementEvaluation,
    Profile,
    User,
)

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


def _profile_score_subquery(profile_id: int) -> Any:
    """Per-profile подзапрос скоринга (фильтр/сортировка по fit_score и score_method)."""
    return (
        select(
            ProcurementEvaluation.procurement_id.label("procurement_id"),
            ProcurementEvaluation.fit_score.label("fit_score"),
            ProcurementEvaluation.score_method.label("score_method"),
        )
        .where(ProcurementEvaluation.profile_id == profile_id)
        .subquery()
    )


def _apply_profile_score(
    row: Procurement, evaluations: list[ProcurementEvaluation], profile_id: int
) -> None:
    """Налагает per-profile результат скоринга на карточку для API-ответа.

    Если для закупки есть оценка под указанного профиль (контекст компетенций/
    вопросов) — базовые колонки ``procurements`` (дефолтный скор) заменяются
    per-profile значениями, а ``rag_report`` подкладывается динамическим атрибутом.
    """
    for evaluation in evaluations:
        if evaluation.profile_id == profile_id:
            row.score = evaluation.score
            row.fit_score = evaluation.fit_score
            row.p_win = evaluation.p_win
            row.margin = evaluation.margin
            row.score_method = evaluation.score_method
            # rag_report — per-user, колонки в procurements нет: подкладываем
            # динамическим атрибутом для API-ответа (ClassVar на Procurement).
            row.rag_report = evaluation.rag_report
            return


class ProcurementRepository:
    """Операции с таблицей ``procurements``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def last_processed_date(
        self,
        platform_id: str,
        now: datetime,
        default_cutoff_days: int,
        *,
        field: str = "publication_date",
    ) -> datetime:
        """Дата последней обработанной записи площадки (MAX(<field>)).

        ``field`` — ``publication_date`` (по умолчанию) или ``update_date``
        (для площадок, поддерживающих дату обновления). Если для площадки ещё
        нет ни одной записи — ``now - default_cutoff_days``.
        """
        column = Procurement.update_date if field == "update_date" else Procurement.publication_date
        stmt = select(func.max(column)).where(Procurement.platform_id == platform_id)
        async with self._db.session() as session:
            max_date = (await session.execute(stmt)).scalar_one_or_none()
        if max_date is None:
            return now - timedelta(days=default_cutoff_days)
        return max_date

    async def get_by_id(
        self, procurement_id: int, profile_id: int | None = None
    ) -> Procurement | None:
        stmt = (
            select(Procurement)
            .where(Procurement.id == procurement_id)
            .options(
                selectinload(Procurement.customer_rel),
                selectinload(Procurement.procedure_type_rel),
                selectinload(Procurement.platform_rel),
            )
        )
        if profile_id is not None:
            stmt = stmt.options(selectinload(Procurement.evaluations))
        async with self._db.session() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
        if row is not None and profile_id is not None:
            _apply_profile_score(row, row.evaluations, profile_id)
        return row

    async def list_procurements(
        self,
        *,
        number: str | None = None,
        platform_id: str | None = None,
        okpd2: str | None = None,
        customer: str | None = None,
        active: bool | None = None,
        min_fit_score: float | None = None,
        scored: bool | None = None,
        sort: str | None = None,
        limit: int = 20,
        offset: int = 0,
        now: datetime | None = None,
        profile_id: int | None = None,
    ) -> tuple[list[Procurement], int]:
        """Возвращает записи и их общее количество по фильтрам.

        ``active`` учитывает текущую дату на стороне клиента: закупка активна,
        если активна по статусу (is_active) И срок актуальности не истёк
        (deadline отсутствует или не раньше ``now``).

        ``sort`` — whitelist-сортировка: ``fit_score`` (по релевантности,
        убывание, NULL-скор в конце) или ``publication_date`` (по дате
        публикации, убывание, NULL в конце). Прочие значения игнорируются —
        используется порядок по id (как в БД).

        ``profile_id`` — скоуп профиля (BR-07): фильтр/сортировка по fit_score и
        score_method применяются к per-profile ``procurement_evaluations``.
        """
        conditions: list[ColumnElement[bool]] = []
        # Per-profile подзапрос скоринга активного профиля: фильтр/сортировка по
        # fit_score и score_method применяются к procurement_evaluations, а не к
        # базовым колонкам procurements (дефолтный скор широкого отбора).
        score_sub = None
        if profile_id is not None:
            score_sub = _profile_score_subquery(profile_id)
        if number:
            conditions.append(Procurement.number.ilike(f"%{number}%"))
        if platform_id:
            conditions.append(Procurement.platform_id == platform_id)
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
        if min_fit_score is not None and score_sub is not None:
            # Релевантность — только по per-profile скорингу (score_sub);
            # без профиля фильтровать нечем (дефолтный скор удалён).
            conditions.append(score_sub.c.fit_score >= min_fit_score)
            conditions.append(score_sub.c.score_method.in_(SCORE_METHOD_STAGES))
        if scored and score_sub is not None:
            # Только закупки с выставленным fit-score (внешний скоринг выполнен):
            # NULL — ещё не обработаны конвейером и в таблицу не попадают.
            conditions.append(score_sub.c.fit_score.is_not(None))

        stmt = select(Procurement).options(
            selectinload(Procurement.customer_rel),
            selectinload(Procurement.procedure_type_rel),
            selectinload(Procurement.platform_rel),
        )
        if score_sub is not None:
            stmt = stmt.join(
                score_sub, Procurement.id == score_sub.c.procurement_id, isouter=True
            ).options(selectinload(Procurement.evaluations))
        if customer:
            stmt = stmt.join(Customer, Procurement.customer_id == Customer.id)
        if sort == "fit_score" and score_sub is not None:
            # Сортировка по Fit — только по per-profile скорингу.
            stmt = stmt.where(*conditions).order_by(
                score_sub.c.fit_score.desc().nullslast(),
                Procurement.id.asc(),
            )
        elif sort == "publication_date":
            stmt = stmt.where(*conditions).order_by(
                Procurement.publication_date.desc().nullslast(),
                Procurement.id.asc(),
            )
        else:
            stmt = stmt.where(*conditions).order_by(Procurement.id.asc())
        count_stmt = select(func.count(Procurement.id)).select_from(Procurement)
        if score_sub is not None:
            count_stmt = count_stmt.join(
                score_sub, Procurement.id == score_sub.c.procurement_id, isouter=True
            )
        count_stmt = count_stmt.where(*conditions)
        if customer:
            count_stmt = count_stmt.join(Customer, Procurement.customer_id == Customer.id)

        async with self._db.session() as session:
            result = await session.execute(stmt.limit(limit).offset(offset))
            rows = list(result.scalars().all())
            total = (await session.execute(count_stmt)).scalar_one()
        if profile_id is not None:
            for row in rows:
                _apply_profile_score(row, row.evaluations, profile_id)
        return rows, total

    async def exists(self, number: str, platform_id: str) -> bool:
        """Проверяет наличие закупки с указанным номером на площадке."""
        stmt = select(Procurement.id).where(
            Procurement.number == number,
            Procurement.platform_id == platform_id,
        )
        async with self._db.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def known_numbers(self, platform_id: str) -> set[str]:
        """Все номера закупок площадки — для пропуска повторной обработки.

        Используется оркестратором, чтобы не открывать детальные страницы уже
        сохранённых закупок при повторных проходах (relevance-режим).
        """
        stmt = select(Procurement.number).where(Procurement.platform_id == platform_id)
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
            stmt = stmt.where(Procurement.platform_id == platform_id)
        async with self._db.session() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def upsert(self, data: dict[str, Any]) -> bool:
        """Записывает закупку.

        Возвращает True, если запись была добавлена; False, если такая закупка
        (number + platform_id) уже существует (повторная запись исключена).

        Реализация: сначала явная проверка существования, затем INSERT. Второй
        уровень защиты — уникальный констрейнт в БД (``uq_procurement_number_platform``).
        """
        number = data.get("number")
        platform_id = data.get("platform_id")
        if not number or not platform_id:
            logger.warning("Пропуск записи: нет number/platform_id")
            return False

        if await self.exists(number, platform_id):
            logger.info("Дубликат: закупка № %s (%s) уже сохранена", number, platform_id)
            return False

        record = Procurement(
            number=str(number),
            platform_id=platform_id,
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
            is_active=bool(data.get("is_active", True)),
            detail_json=data.get("detail_json"),
        )
        async with self._db.session() as session:
            record.customer_id = await self._resolve_customer_id(
                session, data.get("customer"), data.get("inn")
            )
            record.procedure_type_id = await self._resolve_procedure_type_id(
                session, platform_id, data.get("purchase_type")
            )
            session.add(record)
            await session.commit()
        # Отдаём id записи в исходный dict — нужен для постановки задания на внешний
        # скоринг (POST /api/scoring/jobs, ADR-7).
        data["id"] = record.id
        logger.info("Сохранена закупка id=%s (№ %s, %s)", record.id, number, platform_id)
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

    async def _resolve_procedure_type_id(
        self, session: AsyncSession, platform_id: str, name: str | None
    ) -> int | None:
        """Резолвит тип процедуры в канонический ``procedure_types.id``.

        Порядок (гибрид «предзагруженный справочник + fallback»):
        1. маппинг площадки ``procedure_type_mappings`` (platform_id + нормализованное
           родное значение) -> канонический тип;
        2. существующий тип в справочнике с таким же нормализованным именем;
        3. find-or-create (новый «сырой» тип с ``is_canonical=false`` — для значений,
           для которых маппинг ещё не составлен).

        Возвращает ``procedure_types.id`` или None (пустое значение).
        Конкурентные вставки снимаются ``ON CONFLICT``, как для заказчиков (ADR-4).
        """
        normalized = normalize_name(name)
        if not normalized:
            return None

        mapping = (
            await session.execute(
                select(ProcedureTypeMapping)
                .where(
                    ProcedureTypeMapping.platform_id == platform_id,
                    ProcedureTypeMapping.normalized_name == normalized,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if mapping is not None:
            return mapping.procedure_type_id

        existing = (
            await session.execute(
                select(ProcedureType).where(ProcedureType.normalized_name == normalized)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id

        stmt = (
            pg_insert(ProcedureType)
            .values(name=name or normalized, normalized_name=normalized)
            .on_conflict_do_nothing(index_elements=["normalized_name"])
            .returning(ProcedureType.id)
        )
        pid = (await session.execute(stmt)).scalar_one_or_none()
        if pid is not None:
            logger.info(
                "Новый тип процедуры «%s» на площадке %s: нужен маппинг в procedure_type_mappings",
                name,
                platform_id,
            )
            return pid
        # Конфликт: другой процесс уже создал тип — берём существующий.
        obj = (
            await session.execute(
                select(ProcedureType).where(ProcedureType.normalized_name == normalized)
            )
        ).scalar_one()
        return obj.id

    async def mark_scoring_queued(self, procurement_id: int, queued_at: datetime) -> bool:
        """Отмечает закупку как успешно поставленную в очередь внешнего скоринга.

        Возвращает True, если закупка найдена и отметка проставлена. Метку пишем
        только после успешного enqueue: по её отсутствию recovery находит закупки,
        не попавшие в очередь (например, транспорт был недоступен при сохранении).
        """
        async with self._db.session() as session:
            obj = await session.get(Procurement, procurement_id)
            if obj is None:
                return False
            obj.scoring_queued_at = queued_at
            await session.commit()
            return True

    async def find_unscored(
        self, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Закупки, которым требуется (повторная) постановка в очередь скоринга.

        Критерий (recovery после восстановления связи с транспортом):
        - внешний скоринг не выполнен: нет оценки в ``procurement_evaluations``
          с ``fit_score IS NOT NULL`` — задача не дошла до стадии fit или её
          результат не записан;
        - задача не была поставлена (``scoring_queued_at IS NULL``) ИЛИ запись
          обновлялась после постановки (``update_date > scoring_queued_at``) —
          «по времени обновления».

        Просроченные закупки (deadline < now) НЕ исключаются: правила постановки
        в очередь совпадают с правилами записи закупок в БД (см. config_service.yaml
        search_criteria.deadline_not_expired).

        Возвращает список dict'ов: id, number, platform_id, update_date,
        publication_date (для приоритета по времени).
        """
        scored_exists = (
            select(ProcurementEvaluation.id)
            .where(
                ProcurementEvaluation.procurement_id == Procurement.id,
                ProcurementEvaluation.fit_score.is_not(None),
            )
            .exists()
        )
        conditions: list[ColumnElement[bool]] = [
            ~scored_exists,
            or_(
                Procurement.scoring_queued_at.is_(None),
                Procurement.update_date > Procurement.scoring_queued_at,
            ),
        ]
        stmt = (
            select(
                Procurement.id,
                Procurement.number,
                Procurement.platform_id,
                Procurement.update_date,
                Procurement.publication_date,
            )
            .where(*conditions)
            .order_by(Procurement.id.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        return [
            {
                "id": int(r.id),
                "number": r.number,
                "platform_id": r.platform_id,
                "update_date": r.update_date,
                "publication_date": r.publication_date,
            }
            for r in rows
        ]

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

    async def delete_irrelevant(self, min_fit_score: float, profile_id: int | None = None) -> int:
        """Удаляет нерелевантные закупки среди обработанных внешним каскадом скоринга.

        Учитываются ТОЛЬКО записи, прошедшие внешний скоринг (score_method — одна
        из стадий каскада fit/pwin/margin): релевантна закупка с fit_score >= порога,
        нерелевантна — с fit_score < порога (или NULL). Записи без внешнего скоринга
        (default/deadline_expired) не затрагиваются. Заказчики не затрагиваются.
        При ``profile_id`` фильтр применяется к per-profile скорингу профиля.
        """
        if profile_id is not None:
            score_sub = _profile_score_subquery(profile_id)
            stmt = delete(Procurement).where(
                Procurement.id.in_(
                    select(score_sub.c.procurement_id).where(
                        score_sub.c.score_method.in_(SCORE_METHOD_STAGES),
                        or_(
                            score_sub.c.fit_score.is_(None),
                            score_sub.c.fit_score < min_fit_score,
                        ),
                    )
                )
            )
        else:
            # Без профиля определять релевантность нечем (дефолтный скор удалён).
            return 0
        async with self._db.session() as session:
            result = cast("CursorResult[Any]", await session.execute(stmt))
            await session.commit()
        deleted = int(result.rowcount or 0)
        logger.info("Удалено нерелевантных закупок (fit_score < %s): %s", min_fit_score, deleted)
        return deleted

    # ------------------------------------------------------------------ #
    # Пользователи (администратор/тендеролог, вход по логину/паролю)
    # ------------------------------------------------------------------ #
    async def get_user(self, user_id: int) -> User | None:
        async with self._db.session() as session:
            return await session.get(User, user_id)

    async def get_user_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def count_users(self, role: str | None = None) -> int:
        stmt = select(func.count(User.id))
        if role is not None:
            stmt = stmt.where(User.role == role)
        async with self._db.session() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def create_user(
        self, username: str, password_hash: str, role: str, email: str | None = None
    ) -> User:
        user = User(username=username, password_hash=password_hash, role=role, email=email)
        async with self._db.session() as session:
            session.add(user)
            await session.commit()
        logger.info("Создан пользователь %s (роль %s)", username, role)
        return user

    # ------------------------------------------------------------------ #
    # Профили фильтрации пользователя (tenant-скоуп BR-07)
    # ------------------------------------------------------------------ #
    DEFAULT_PROFILE_NAME = "default"

    async def first_user(self) -> User | None:
        """Первый пользователь (сервис-аккаунт для dev-режима и конвейера)."""
        stmt = select(User).order_by(User.id.asc()).limit(1)
        async with self._db.session() as session:
            result: User | None = (await session.execute(stmt)).scalar_one_or_none()
            return result

    async def backfill_orphaned_profiles(self, user_id: int) -> int:
        """Присваивает профили без ``user_id`` указанному пользователю (идемпотентно).

        Нужно для миграции 1.29: существующие профили (глобальные) после перехода
        на мультитенантность не имеют владельца — сервис-аккаунт забирает их на старте.
        """
        async with self._db.session() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(Profile).where(Profile.user_id.is_(None)).values(user_id=user_id)
                ),
            )
            await session.commit()
        count = int(result.rowcount or 0)
        if count:
            logger.info(
                "Осиротевшие профили (user_id IS NULL) присвоены пользователю %s: %s",
                user_id,
                count,
            )
        return count

    async def get_profile(self, user_id: int, profile_id: int) -> Profile | None:
        stmt = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_profile_by_name(self, user_id: int, name: str) -> Profile | None:
        stmt = select(Profile).where(Profile.user_id == user_id, Profile.name == name)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_profiles(
        self, user_id: int, limit: int = 100, offset: int = 0
    ) -> tuple[list[Profile], int]:
        stmt = (
            select(Profile)
            .where(Profile.user_id == user_id)
            .order_by(Profile.id.asc())
            .limit(limit)
            .offset(offset)
        )
        count_stmt = select(func.count(Profile.id)).where(Profile.user_id == user_id)
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
            total = int((await session.execute(count_stmt)).scalar_one())
        return rows, total

    async def get_active_profile(self, user_id: int) -> Profile | None:
        """Активный профиль пользователя (per-user состояние).

        Приоритет: 1) ``is_active=true``; 2) профиль ``default``; 3) первый включённый.
        Один запрос (ORDER BY + LIMIT 1). Полностью отключённые профили, не
        являющиеся default, не возвращаются.
        """
        stmt = (
            select(Profile)
            .where(
                Profile.user_id == user_id,
                or_(
                    Profile.is_active.is_(True),
                    Profile.name == self.DEFAULT_PROFILE_NAME,
                    Profile.enabled.is_(True),
                ),
            )
            .order_by(
                Profile.is_active.desc(),
                (Profile.name == self.DEFAULT_PROFILE_NAME).desc(),
                Profile.id.asc(),
            )
            .limit(1)
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def set_active_profile(self, user_id: int, profile_id: int) -> Profile:
        """Делает профиль активным (сбрасывает остальные у пользователя)."""
        async with self._db.session() as session:
            stmt = select(Profile).where(Profile.user_id == user_id, Profile.id == profile_id)
            profile = (await session.execute(stmt)).scalar_one_or_none()
            if profile is None:
                raise ValueError("Профиль не найден у пользователя")
            await session.execute(
                update(Profile).where(Profile.user_id == user_id).values(is_active=False)
            )
            profile.is_active = True
            await session.commit()
            # updated_at (server onupdate) генерируется в БД: с expire_on_commit=False
            # SQLAlchemy не подставляет его в объект без refresh. После выхода из
            # сессии объект detached, и _profile_out упадёт с DetachedInstanceError.
            await session.refresh(profile)
            return profile

    async def delete_profile(self, user_id: int, profile_id: int) -> None:
        """Удаляет профиль пользователя.

        Нельзя удалить последний профиль пользователя или активный (сначала
        активируйте другой). Оценки профиля (procurement_evaluations) и слова
        (keywords) удаляются каскадом (FK ON DELETE CASCADE).
        """
        async with self._db.session() as session:
            profile = (
                await session.execute(
                    select(Profile).where(Profile.user_id == user_id, Profile.id == profile_id)
                )
            ).scalar_one_or_none()
            if profile is None:
                raise ValueError("Профиль не найден у пользователя")
            total = await session.scalar(
                select(func.count()).select_from(Profile).where(Profile.user_id == user_id)
            )
            if total is not None and total <= 1:
                raise ValueError("Нельзя удалить последний профиль пользователя")
            if profile.is_active:
                raise ValueError("Сначала активируйте другой профиль")
            await session.delete(profile)
            await session.commit()
            logger.info("Удалён профиль %s (id=%s, user_id=%s)", profile.name, profile_id, user_id)

    async def set_profile_keywords(
        self, profile_id: int, keywords: list[str], exclusion_words: list[str]
    ) -> None:
        """Переписывает ключевые слова профиля в таблицу ``keywords`` (канонический источник).

        ``type`` = ``keyword`` (позитивные) или ``exclusion`` (слова-исключения).
        Слова НЕ хранятся в JSONB-полях профиля (ER: PROFILE -> KEYWORD).
        Уникальность (profile_id, word, type) — как в миграции 1.30.
        """
        async with self._db.session() as session:
            await session.execute(delete(Keyword).where(Keyword.profile_id == profile_id))
            rows: list[Keyword] = [
                Keyword(profile_id=profile_id, word=word, type=kind)
                for kind, words in (
                    ("keyword", keywords or []),
                    ("exclusion", exclusion_words or []),
                )
                for word in dict.fromkeys(words)
            ]
            session.add_all(rows)
            await session.commit()

    async def get_profile_keywords(self, profile_id: int) -> dict[str, list[str]]:
        """Возвращает ключевые слова профиля из таблицы ``keywords`` (канонический источник)."""
        stmt = select(Keyword).where(Keyword.profile_id == profile_id).order_by(Keyword.id)
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        return {
            "keywords": [r.word for r in rows if r.type == "keyword"],
            "exclusion_words": [r.word for r in rows if r.type == "exclusion"],
        }

    async def list_profiles_keywords(
        self, profile_ids: list[int]
    ) -> dict[int, dict[str, list[str]]]:
        """Батч-чтение слов нескольких профилей одним запросом (без N+1)."""
        if not profile_ids:
            return {}
        stmt = (
            select(Keyword)
            .where(Keyword.profile_id.in_(profile_ids))
            .order_by(Keyword.profile_id, Keyword.id)
        )
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        result: dict[int, dict[str, list[str]]] = {}
        for row in rows:
            bucket = result.setdefault(row.profile_id, {"keywords": [], "exclusion_words": []})
            key = "keywords" if row.type == "keyword" else "exclusion_words"
            bucket[key].append(row.word)
        return result

    async def ensure_default_profile(self, user_id: int) -> Profile:
        """Возвращает default-профиль пользователя, создавая пустой, если его нет.

        Ключевые слова НЕ заполняются автоматически — их загружает скрипт
        ``seed-profile`` (R8) по явной команде оператора (data/profile.md).
        """
        async with self._db.session() as session:
            profile = (
                await session.execute(
                    select(Profile).where(
                        Profile.user_id == user_id, Profile.name == self.DEFAULT_PROFILE_NAME
                    )
                )
            ).scalar_one_or_none()
            if profile is None:
                profile = Profile(
                    name=self.DEFAULT_PROFILE_NAME,
                    user_id=user_id,
                    enabled=True,
                    is_active=True,
                    competencies="",
                )
                session.add(profile)
                await session.commit()
        return profile

    async def seed_default_profile(self, user_id: int, seed: dict[str, Any]) -> Profile:
        """Создаёт/обновляет активный профиль ``default`` пользователя (R8).

        ``seed`` — как в ``upsert_profile`` (+ ``keywords``/``exclusion_words``,
        которые пишутся в таблицу ``keywords``).
        """
        profile = await self.upsert_profile({**seed, "name": "default"}, user_id)
        return profile

    async def upsert_profile(self, data: dict[str, Any], user_id: int) -> Profile:
        """Создаёт или обновляет профиль пользователя (ключ — user_id + name).

        Ключевые слова/слова-исключения из ``data`` записываются в таблицу
        ``keywords`` (канонический источник), а не в JSONB-поля профиля.
        При ``is_active=true`` остальные профили пользователя деактивируются
        (гарантия единственного активного профиля).
        """
        name = data.get("name")
        if not name:
            raise ValueError("profiles.name обязателен")
        wants_keywords = "keywords" in data or "exclusion_words" in data
        async with self._db.session() as session:
            profile = (
                await session.execute(
                    select(Profile).where(Profile.user_id == user_id, Profile.name == name)
                )
            ).scalar_one_or_none()
            if profile is None:
                profile = Profile(name=name, user_id=user_id)
                session.add(profile)
            if "enabled" in data:
                profile.enabled = bool(data["enabled"])
            if "competencies" in data:
                profile.competencies = str(data["competencies"])
            if "questions" in data:
                profile.questions = list(data["questions"])
            if "target_etp" in data:
                profile.target_etp = list(data["target_etp"])
            if "target_laws" in data:
                profile.target_laws = list(data["target_laws"])
            if "min_fit_threshold" in data:
                profile.min_fit_threshold = data["min_fit_threshold"]
            if "okpd_codes" in data:
                profile.okpd_codes = list(data["okpd_codes"])
            if "nmck_min" in data:
                profile.nmck_min = data["nmck_min"]
            if "nmck_max" in data:
                profile.nmck_max = data["nmck_max"]
            if "active_only" in data:
                profile.active_only = bool(data["active_only"])
            # Профиль становится активным: явно (is_active=true) или по умолчанию
            # для профиля «default» (per-user состояние, BR-07).
            wants_active = data.get("is_active")
            if wants_active or (wants_active is None and name == self.DEFAULT_PROFILE_NAME):
                await session.execute(
                    update(Profile).where(Profile.user_id == user_id).values(is_active=False)
                )
                profile.is_active = True
            await session.commit()
            # updated_at (server onupdate) генерируется в БД: с expire_on_commit=False
            # SQLAlchemy не подставляет его в объект без refresh. После выхода из
            # сессии объект detached, и _profile_out упадёт с DetachedInstanceError.
            await session.refresh(profile)
        if wants_keywords:
            await self.set_profile_keywords(
                profile.id,
                data.get("keywords", []),
                data.get("exclusion_words", []),
            )
        logger.info("Сохранён профиль %s (id=%s, user_id=%s)", name, profile.id, user_id)
        return profile

    # ------------------------------------------------------------------ #
    # Per-profile результаты скоринга (BR-07)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _find_or_create_evaluation(
        session: Any, procurement_id: int, profile_id: int
    ) -> ProcurementEvaluation:
        """Find-or-create per-profile оценки в ОТКРЫТОЙ сессии (без commit)."""
        existing: ProcurementEvaluation | None = (
            session.execute(
                select(ProcurementEvaluation).where(
                    ProcurementEvaluation.procurement_id == procurement_id,
                    ProcurementEvaluation.profile_id == profile_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ProcurementEvaluation(procurement_id=procurement_id, profile_id=profile_id)
            session.add(existing)
        return existing

    async def upsert_score(
        self,
        procurement_id: int,
        profile_id: int,
        *,
        score: float | None = None,
        fit_score: float | None = None,
        p_win: float | None = None,
        margin: float | None = None,
        score_method: str = "default",
        rag_report: dict[str, Any] | None = None,
    ) -> ProcurementEvaluation:
        """Обновляет/создаёт per-profile результат скоринга закупки."""
        async with self._db.session() as session:
            evaluation = self._find_or_create_evaluation(session, procurement_id, profile_id)
            if score is not None:
                evaluation.score = _round_score(score)
            if fit_score is not None:
                evaluation.fit_score = _round_score(fit_score)
            if p_win is not None:
                evaluation.p_win = _round_score(p_win)
            if margin is not None:
                evaluation.margin = _round_score(margin)
            evaluation.score_method = score_method
            if rag_report is not None:
                evaluation.rag_report = rag_report
            await session.commit()
            return evaluation

    async def get_score(self, procurement_id: int, profile_id: int) -> ProcurementEvaluation | None:
        stmt = select(ProcurementEvaluation).where(
            ProcurementEvaluation.procurement_id == procurement_id,
            ProcurementEvaluation.profile_id == profile_id,
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def update_rag_report(
        self, procurement_id: int, profile_id: int, rag_report: dict[str, Any]
    ) -> ProcurementEvaluation:
        """Сохраняет RAG-отчёт анализа стоп-условий (не меняя score_method)."""
        async with self._db.session() as session:
            evaluation = self._find_or_create_evaluation(session, procurement_id, profile_id)
            evaluation.rag_report = rag_report
            await session.commit()
        return evaluation
