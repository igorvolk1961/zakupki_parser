"""Операции репозитория с закупками, принятыми «в работу» (Эпик 5, US-5.4–5.6).

Признак «в работе» — per-profile (``procurement_work_items.profile_id``, BR-07).
Запись переживает удаление закупки из общей базы: FK ``procurement_id`` имеет
``ON DELETE SET NULL``, ключевые поля карточки хранятся снимком в самой записи.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult

from zakupki_parser.storage.db import (
    Procurement,
    ProcurementWorkItem,
)
from zakupki_parser.storage.repository.base import RepositoryMixin

logger = logging.getLogger(__name__)


class WorkMixin(RepositoryMixin):
    """Закупки «в работе» профиля (``procurement_work_items``)."""

    async def accept_into_work(
        self,
        procurement_id: int,
        profile_id: int,
        *,
        source: str = "search",
        notes: str | None = None,
    ) -> ProcurementWorkItem | None:
        """Принимает закупку «в работу» (find-or-create, идемпотентно).

        Снимок ключевых полей берётся из текущей карточки закупки; при повторном
        принятии (та же пара) снимок обновляется. Возвращает None, если закупка
        с таким id не найдена.
        """
        async with self._db.session() as session:
            row = (
                await session.execute(select(Procurement).where(Procurement.id == procurement_id))
            ).scalar_one_or_none()
        if row is None:
            return None
        snapshot = {
            "number": row.number,
            "platform_id": row.platform_id,
            "url": row.url,
            "subject": row.subject,
            "nmck": row.nmck,
            "deadline": row.deadline,
            "law": row.law,
            "customer_name": row.customer_rel.name if row.customer_rel is not None else None,
        }
        stmt = (
            pg_insert(ProcurementWorkItem)
            .values(
                profile_id=profile_id,
                procurement_id=procurement_id,
                source=source,
                notes=notes,
                **snapshot,
            )
            .on_conflict_do_update(
                index_elements=["profile_id", "procurement_id"],
                set_={**snapshot, "source": source, "notes": notes},
            )
            .returning(ProcurementWorkItem.id)
        )
        async with self._db.session() as session:
            item_id = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
        if item_id is None:
            return None
        logger.info(
            "Закупка %s принята «в работу» профилем %s (источник %s)",
            procurement_id,
            profile_id,
            source,
        )
        return await self.get_work_item(profile_id, procurement_id)

    async def accept_into_work_by_url(
        self,
        url: str,
        profile_id: int,
        *,
        notes: str | None = None,
    ) -> ProcurementWorkItem:
        """Принимает «в работу» закупку по URL ЭТП.

        Если закупка с таким URL уже есть в ``procurements`` — привязывается к ней
        (со снимком карточки); иначе создаётся запись с ``procurement_id=NULL`` и
        снимком из URL (закупка может появиться в базе позже, при обходе парсера).
        """
        existing = await self.find_by_url(url)
        if existing is not None:
            item = await self.accept_into_work(existing.id, profile_id, source="url", notes=notes)
            if item is not None:
                return item
        # Закупки в базе нет: фиксируем «в работе» по URL (снимок, без FK на закупку).
        stmt = (
            pg_insert(ProcurementWorkItem)
            .values(
                profile_id=profile_id,
                procurement_id=None,
                source="url",
                notes=notes,
                url=url,
            )
            .on_conflict_do_nothing(index_elements=["profile_id", "procurement_id"])
            .returning(ProcurementWorkItem.id)
        )
        async with self._db.session() as session:
            item_id = (await session.execute(stmt)).scalar_one_or_none()
            if item_id is None:
                # Редкий конфликт/параллелизм: читаем существующую запись-снимок по URL.
                item = await self._find_work_snapshot(session, profile_id, url)
                await session.commit()
                if item is not None:
                    return item
                raise RuntimeError(f"Не удалось принять закупку по URL {url}")
            await session.commit()
        logger.info("Закупка по URL %s принята «в работу» профилем %s", url, profile_id)
        item = await self._find_work_snapshot_outer(profile_id, url)
        if item is None:  # pragma: no cover - строка только что создана выше
            raise RuntimeError(f"Не удалось прочитать запись по URL {url}")
        return item

    async def list_work_items(self, profile_id: int) -> list[ProcurementWorkItem]:
        """Закупки «в работе» профиля, новые — первыми."""
        stmt = (
            select(ProcurementWorkItem)
            .where(ProcurementWorkItem.profile_id == profile_id)
            .order_by(ProcurementWorkItem.accepted_at.desc(), ProcurementWorkItem.id.desc())
        )
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def count_work_items(self, profile_id: int) -> int:
        """Число закупок «в работе» профиля."""
        stmt = select(func.count(ProcurementWorkItem.id)).where(
            ProcurementWorkItem.profile_id == profile_id
        )
        async with self._db.session() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def get_work_item(
        self, profile_id: int, procurement_id: int
    ) -> ProcurementWorkItem | None:
        """Запись «в работе» по паре (profile_id, procurement_id)."""
        stmt = select(ProcurementWorkItem).where(
            ProcurementWorkItem.profile_id == profile_id,
            ProcurementWorkItem.procurement_id == procurement_id,
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def remove_from_work(self, profile_id: int, procurement_id: int) -> bool:
        """Снимает закупку с «в работе» (удаляется только запись, не закупка)."""
        stmt = delete(ProcurementWorkItem).where(
            ProcurementWorkItem.profile_id == profile_id,
            ProcurementWorkItem.procurement_id == procurement_id,
        )
        async with self._db.session() as session:
            cursor = cast(
                "CursorResult[Any]",
                await session.execute(stmt),
            )
            await session.commit()
        removed = int(cursor.rowcount or 0) > 0
        if removed:
            logger.info("Закупка %s снята с «в работе» профилем %s", procurement_id, profile_id)
        return removed

    async def remove_work_item(self, profile_id: int, work_item_id: int) -> bool:
        """Удаляет запись «в работе» по её id (профильный скоуп BR-07).

        Используется для записей-снимков без закупки (``procurement_id IS NULL``),
        у которых нет ключа по закупке.
        """
        stmt = delete(ProcurementWorkItem).where(
            ProcurementWorkItem.id == work_item_id,
            ProcurementWorkItem.profile_id == profile_id,
        )
        async with self._db.session() as session:
            cursor = cast(
                "CursorResult[Any]",
                await session.execute(stmt),
            )
            await session.commit()
        removed = int(cursor.rowcount or 0) > 0
        if removed:
            logger.info("Запись «в работе» %s удалена (профиль %s)", work_item_id, profile_id)
        return removed

    async def find_by_url(self, url: str) -> Procurement | None:
        """Закупка с точным совпадением URL (для приёма по ссылке ЭТП)."""
        stmt = select(Procurement).where(Procurement.url == url).limit(1)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def _find_work_snapshot(
        self, session: Any, profile_id: int, url: str
    ) -> ProcurementWorkItem | None:
        """Запись-снимок (procurement_id IS NULL) по URL в ОТКРЫТОЙ сессии."""
        stmt = select(ProcurementWorkItem).where(
            ProcurementWorkItem.profile_id == profile_id,
            ProcurementWorkItem.url == url,
            ProcurementWorkItem.procurement_id.is_(None),
        )
        result = (await session.execute(stmt)).scalars().first()
        return cast(ProcurementWorkItem | None, result)

    async def _find_work_snapshot_outer(
        self, profile_id: int, url: str
    ) -> ProcurementWorkItem | None:
        """Запись-снимок (procurement_id IS NULL) по URL (своя сессия)."""
        async with self._db.session() as session:
            return await self._find_work_snapshot(session, profile_id, url)
