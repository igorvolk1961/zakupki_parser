"""Операции репозитория с заказчиками (справочник, ADR-4)."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from zakupki_parser.storage.db import Customer
from zakupki_parser.storage.repository.base import RepositoryMixin

logger = logging.getLogger(__name__)


class CustomerMixin(RepositoryMixin):
    """Заказчики (``customers``): чтение и рейтинг."""

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
