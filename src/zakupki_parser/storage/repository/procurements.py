"""Операции репозитория с закупками: запись, чтение и обслуживание."""

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
    Platform,
    ProcedureType,
    ProcedureTypeMapping,
    Procurement,
    ProcurementEvaluation,
)
from zakupki_parser.storage.repository.base import (
    RepositoryMixin,
    _apply_profile_score,
    _profile_score_subquery,
)

logger = logging.getLogger(__name__)


class ProcurementMixin(RepositoryMixin):
    """Закупки (``procurements``) и справочники процедур/заказчиков при записи."""

    async def list_platforms(self) -> list[dict[str, Any]]:
        """Справочник платформ из БД: ключ, наименование, URL и активность.

        Единственный источник данных о площадках — таблица ``platforms`` (сид из
        конфигов выполняется миграциями; активность синхронизируется из
        config_service.yaml при старте/сохранении конфигурации).
        """
        stmt = select(Platform.platform_id, Platform.name, Platform.url, Platform.enabled).order_by(
            Platform.platform_id
        )
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        return [
            {"value": pid, "label": pid, "name": name, "url": url, "enabled": bool(enabled)}
            for pid, name, url, enabled in rows
        ]

    async def enabled_platform_ids(self) -> set[str]:
        """Идентификаторы активных площадок (``platforms.enabled``)."""
        stmt = select(Platform.platform_id).where(Platform.enabled.is_(True))
        async with self._db.session() as session:
            return {row[0] for row in (await session.execute(stmt)).all()}

    async def sync_platform_enabled(self, enabled_ids: set[str]) -> None:
        """Синхронизирует ``platforms.enabled`` с config_service.yaml.

        Конфиг — редактируемый интерфейс, БД — источник истины: активность
        площадки вычисляется из ``sites[].enabled`` и записывается в справочник,
        чтобы не было расхождений между конфигом и БД.
        """
        if not enabled_ids:
            stmt = update(Platform).values(enabled=False)
        else:
            stmt = update(Platform).values(enabled=Platform.platform_id.in_(enabled_ids))
        async with self._db.session() as session:
            await session.execute(stmt)
            await session.commit()

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

    async def find_id(self, number: str, platform_id: str) -> int | None:
        """id существующей закупки по номеру+площадке (или None)."""
        stmt = select(Procurement.id).where(
            Procurement.number == number,
            Procurement.platform_id == platform_id,
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def exists(self, number: str, platform_id: str) -> bool:
        """Проверяет наличие закупки с указанным номером на площадке."""
        return await self.find_id(number, platform_id) is not None

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
            # Номер площадки — обязательный бизнес-ключ (nullable=False + unique). Отсутствие
            # ключа означает сбой парсинга, а не штатную ситуацию: запись не может быть ни
            # сохранена, ни дедуплицирована, ни поставлена в очередь скоринга. Фиксируем как
            # ошибку с контекстом, чтобы причину можно было найти по логу (сравните: ранее —
            # тихий WARNING без контекста, маскирующий потерю закупки).
            logger.error(
                "Закупка без обязательного ключа пропущена при записи: "
                "number=%r platform_id=%r url=%s subject=%r",
                number,
                platform_id,
                data.get("url"),
                data.get("subject"),
            )
            return False

        existing_id = await self.find_id(number, platform_id)
        if existing_id is not None:
            # Отдаём id существующей записи в исходный dict — нужен для записи
            # per-profile оценки (matched_keywords) другого профиля, оценивающего
            # уже сохранённую закупку (мультипрофильный обход, BR-07).
            data["id"] = existing_id
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
            # Контекст досборки деталей ПОСЛЕ скоринга (BR-08): api_fields сохраняются
            # при персисте на уровне списка; NULL — досборка не требуется.
            detail_api=data.get("detail_api") or None,
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

    async def update_details(self, procurement_id: int, data: dict[str, Any]) -> bool:
        """Обновляет карточку закупки деталями, дособранными ПОСЛЕ записи (BR-08).

        Персист на уровне списка сохраняет только поля списка; ОКПД2/файлы/ИНН/статус/
        НМЦК догружаются отдельным проходом и обновляют существующую запись (без
        дублирования — быстрый ``UPDATE``, в отличие от ``upsert``, который отбрасывает
        повторную запись). Возвращает True, если запись найдена и обновлена.
        """
        detail = data.get("detail_json")
        async with self._db.session() as session:
            record = await session.get(Procurement, procurement_id)
            if record is None:
                return False
            record.subject = data.get("subject") or record.subject
            record.nmck = data.get("nmck") if data.get("nmck") is not None else record.nmck
            record.okpd2_codes = data.get("okpd2_codes") or data.get("okpd2_code")
            record.kpgz_codes = data.get("kpgz_codes") or data.get("kpgz_code")
            if data.get("files_json") is not None:
                record.files_json = data["files_json"]
            if detail is not None:
                record.detail_json = detail
            record.is_active = bool(data.get("is_active", record.is_active))
            if data.get("inn") and record.customer_id is None:
                record.customer_id = await self._resolve_customer_id(
                    session, data.get("customer"), data.get("inn")
                )
            record.details_fetched_at = datetime.now(UTC)
            await session.commit()
        logger.info("Обновлена карточка закупки id=%s (детали дособраны)", procurement_id)
        return True

    async def find_scored_without_details(
        self, platform_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Закупки площадки, получившие результат скоринга, но без деталей (BR-08).

        Детали с площадки дособираются отдельным проходом ТОЛЬКО ПОСЛЕ того, как
        парсер получил результат скоринга (``procurement_evaluations.fit_score IS
        NOT NULL`` — внешний сервис вернул результат через POST /score). Возвращает
        записи с ``detail_api IS NOT NULL`` (есть контекст досборки) и
        ``details_fetched_at IS NULL`` (досборка ещё не выполнена).
        """
        scored = (
            select(ProcurementEvaluation.procurement_id)
            .where(ProcurementEvaluation.fit_score.is_not(None))
            .distinct()
        )
        conditions: list[ColumnElement[bool]] = [
            Procurement.platform_id == platform_id,
            Procurement.detail_api.is_not(None),
            Procurement.details_fetched_at.is_(None),
            Procurement.id.in_(scored),
        ]
        stmt = (
            select(
                Procurement.id,
                Procurement.number,
                Procurement.url,
                Procurement.detail_api,
                Procurement.detail_json,
            )
            .where(*conditions)
            .order_by(Procurement.update_date.desc().nullslast())
        )
        if limit:
            stmt = stmt.limit(limit)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        return [
            {
                "id": row.id,
                "number": row.number,
                "url": row.url,
                "detail_api": row.detail_api,
                "detail_json": row.detail_json,
            }
            for row in rows
        ]

    async def mark_details_fetched(self, procurement_id: int) -> bool:
        """Отмечает закупку как получившую дособранные детали (BR-08)."""
        async with self._db.session() as session:
            cursor = cast(
                CursorResult[Any],
                await session.execute(
                    update(Procurement)
                    .where(Procurement.id == procurement_id)
                    .values(details_fetched_at=datetime.now(UTC))
                ),
            )
            await session.commit()
            return (cursor.rowcount or 0) > 0

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

    async def mark_scoring_queued(
        self, procurement_id: int, profile_id: int, queued_at: datetime
    ) -> bool:
        """Отмечает пару (закупка, профиль) как успешно поставленную в очередь.

        Per-profile (BR-07): метка пишется в ``procurement_evaluations`` по
        ``(procurement_id, profile_id)`` — только после успешного enqueue. По её
        отсутствию recovery находит (закупка, профиль), не попавшие в очередь
        (например, транспорт был недоступен при сохранении).
        """
        async with self._db.session() as session:
            cursor = cast(
                CursorResult[Any],
                await session.execute(
                    update(ProcurementEvaluation)
                    .where(
                        ProcurementEvaluation.procurement_id == procurement_id,
                        ProcurementEvaluation.profile_id == profile_id,
                    )
                    .values(scoring_queued_at=queued_at)
                ),
            )
            await session.commit()
            return (cursor.rowcount or 0) > 0

    async def mark_scoring_iteration(self, procurement_id: int, iteration: int) -> bool:
        """Зафиксировать номер итерации цикла, в которую закупка поставлена в очередь.

        Пишется в ``procurements.scoring_iteration`` (база журнала «Метрики»):
        каждая закупка группируется по тому проходу планировщика, который её
        отобрал. Идемпотентно: повторная постановка (recovery) обновляет номер.
        """
        async with self._db.session() as session:
            cursor = cast(
                CursorResult[Any],
                await session.execute(
                    update(Procurement)
                    .where(Procurement.id == procurement_id)
                    .values(scoring_iteration=iteration)
                ),
            )
            await session.commit()
            return (cursor.rowcount or 0) > 0

    async def find_unscored(
        self,
        limit: int | None = None,
        queued_before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Пары (закупка, профиль), которым требуется (повторная) постановка в очередь.

        Пер-профильный recovery (BR-07): критерий — по ``procurement_evaluations``,
        а не по закупке целиком. Условия:
        - профиль отобрал закупку (``matched_keywords IS NOT NULL``);
        - для ЭТОГО профиля результат fit не записан (``fit_score IS NULL``);
        - задача не поставлена (``scoring_queued_at IS NULL``) ИЛИ закупка
          обновлялась после постановки (``update_date > scoring_queued_at``);
        - при ``queued_before`` — метка постановки старше порога
          (``scoring_queued_at < queued_before``): задание могло быть потеряно.

        Возвращает список dict'ов: id, profile_id, number, platform_id, update_date,
        publication_date (priority — по времени обновления/публикации).
        """
        conditions: list[ColumnElement[bool]] = [
            ProcurementEvaluation.profile_id.is_not(None),
            ProcurementEvaluation.matched_keywords.is_not(None),
            ProcurementEvaluation.fit_score.is_(None),
            or_(
                ProcurementEvaluation.scoring_queued_at.is_(None),
                Procurement.update_date > ProcurementEvaluation.scoring_queued_at,
                *(
                    [ProcurementEvaluation.scoring_queued_at < queued_before]
                    if queued_before is not None
                    else []
                ),
            ),
        ]
        stmt = (
            select(
                ProcurementEvaluation.procurement_id,
                ProcurementEvaluation.profile_id,
                Procurement.number,
                Procurement.platform_id,
                Procurement.update_date,
                Procurement.publication_date,
            )
            .join(Procurement, Procurement.id == ProcurementEvaluation.procurement_id)
            .where(*conditions)
            .order_by(Procurement.update_date.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        return [
            {
                "id": int(r.procurement_id),
                "profile_id": int(r.profile_id),
                "number": r.number,
                "platform_id": r.platform_id,
                "update_date": r.update_date,
                "publication_date": r.publication_date,
            }
            for r in rows
        ]

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
