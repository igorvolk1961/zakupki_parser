"""Интеграционные тесты нормализации заказчиков (ADR-4, требуют PostgreSQL).

Запускаются, если задан ``ZAKUPKI_TEST_DSN`` (иначе — skip).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")


@pytest_asyncio.fixture
async def db() -> AsyncIterator[Database]:
    engine = create_async_engine(TEST_DSN)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    database = Database(DbConfig(dsn=TEST_DSN, enabled=True))
    await database.connect()
    yield database
    await database.dispose()


async def _customer_count(db: Database) -> int:
    async with db.session() as session:
        result = await session.execute(text("SELECT count(*) FROM customers"))
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_upsert_creates_customer_and_links(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert(
        {
            "number": "C-1",
            "platform_id": "zakupki_mos",
            "customer": "ООО Ромашка",
            "inn": "3903007130",
            "subject": "x",
        }
    )
    async with db.session() as session:
        result = await session.execute(
            text(
                "SELECT p.customer_id, c.normalized_name, c.inn "
                "FROM procurements p JOIN customers c ON c.id = p.customer_id "
                "WHERE p.number = 'C-1'"
            )
        )
        cid, norm, inn = result.one()
    assert cid is not None
    assert norm == "ооо ромашка"
    assert inn == "3903007130"


@pytest.mark.asyncio
async def test_same_normalized_name_reuses_customer(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "C-2", "platform_id": "zakupki_mos", "customer": "ООО Ромашка"})
    await repo.upsert({"number": "C-3", "platform_id": "zakupki_mos", "customer": "ООО   Ромашка "})
    assert await _customer_count(db) == 1
    async with db.session() as session:
        result = await session.execute(
            text(
                "SELECT count(DISTINCT customer_id) FROM procurements WHERE number IN ('C-2','C-3')"
            )
        )
        assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_inn_backfilled_on_existing_customer(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "C-4", "platform_id": "zakupki_mos", "customer": "ООО Ромашка"})
    await repo.upsert(
        {
            "number": "C-5",
            "platform_id": "zakupki_mos",
            "customer": "ООО Ромашка",
            "inn": "3903007130",
        }
    )
    assert await _customer_count(db) == 1
    async with db.session() as session:
        result = await session.execute(
            text("SELECT inn FROM customers WHERE normalized_name = 'ооо ромашка'")
        )
        assert result.scalar_one() == "3903007130"


@pytest.mark.asyncio
async def test_empty_customer_name_leaves_customer_id_null(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "C-6", "platform_id": "zakupki_mos", "customer": ""})
    assert await _customer_count(db) == 0
    async with db.session() as session:
        result = await session.execute(
            text("SELECT customer_id FROM procurements WHERE number = 'C-6'")
        )
        assert result.scalar_one() is None


@pytest.mark.asyncio
async def test_two_distinct_names_two_customers(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "C-7", "platform_id": "zakupki_mos", "customer": "ООО Ромашка"})
    await repo.upsert({"number": "C-8", "platform_id": "zakupki_mos", "customer": "АО ТехноЛогика"})
    assert await _customer_count(db) == 2


@pytest.mark.asyncio
async def test_list_procurements_filter_by_customer(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "C-9", "platform_id": "zakupki_mos", "customer": "ООО Ромашка"})
    await repo.upsert(
        {"number": "C-10", "platform_id": "zakupki_mos", "customer": "АО ТехноЛогика"}
    )
    rows, total = await repo.list_procurements(customer="ромашка")
    assert total == 1
    assert rows[0].number == "C-9"
    # Имя заказчика доступно через связь.
    assert rows[0].customer_rel is not None
    assert rows[0].customer_rel.name == "ООО Ромашка"


@pytest.mark.asyncio
async def test_rating_set_and_read(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "C-11", "platform_id": "zakupki_mos", "customer": "ООО Ромашка"})
    cust = await repo.list_customers(name="Ромашка")
    cid = cust[0][0].id
    assert await repo.set_customer_rating(cid, 0.85) is True
    fetched = await repo.get_customer(cid)
    assert fetched is not None and fetched.rating == 0.85
    assert await repo.set_customer_rating(999999, 1.0) is False


@pytest.mark.asyncio
async def test_clear_all(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "CL-1", "platform_id": "zakupki_mos", "customer": "ООО Ромашка"})
    await repo.upsert(
        {"number": "CL-2", "platform_id": "zakupki_mos", "customer": "АО ТехноЛогика"}
    )
    deleted = await repo.clear_all()
    assert deleted["procurements"] == 2
    assert deleted["customers"] == 2
    assert await _customer_count(db) == 0
    async with db.session() as session:
        result = await session.execute(
            text("SELECT count(*) FROM procurements WHERE number IN ('CL-1','CL-2')")
        )
        assert result.scalar_one() == 0


@pytest.mark.asyncio
async def test_backfill_from_legacy_customer_column(db: Database) -> None:
    """Валидирует SQL-логику backfill из миграции 1.13 на «легаси»-данных."""
    engine = create_async_engine(TEST_DSN)
    async with engine.begin() as conn:
        # Легаси-состояние: денормализованная колонка customer.
        await conn.execute(text("ALTER TABLE procurements ADD COLUMN customer text"))
        await conn.execute(
            text(
                "INSERT INTO procurements (number, platform_id, customer, subject) "
                "VALUES (:n1, 'zakupki_mos', :c1, 'a'), (:n2, 'zakupki_mos', :c2, 'b')"
            ),
            {"n1": "L-1", "c1": "ООО Ромашка", "n2": "L-2", "c2": "ООО   Ромашка "},
        )
        # Тот же backfill-SQL, что в db.changelog-1.13.yaml.
        await conn.execute(
            text(
                "INSERT INTO customers (name, normalized_name) "
                "SELECT DISTINCT ON (lower(trim(regexp_replace(customer, "
                "'[[:space:]]+', ' ', 'g')))) customer, "
                "lower(trim(regexp_replace(customer, '[[:space:]]+', ' ', 'g'))) "
                "FROM procurements "
                "WHERE customer IS NOT NULL AND trim(customer) <> '' "
                "ORDER BY lower(trim(regexp_replace(customer, '[[:space:]]+', ' ', 'g')))"
            )
        )
        await conn.execute(
            text(
                "UPDATE procurements p SET customer_id = c.id FROM customers c "
                "WHERE lower(trim(regexp_replace(p.customer, '[[:space:]]+', ' ', 'g'))) = "
                "c.normalized_name"
            )
        )
    await engine.dispose()

    assert await _customer_count(db) == 1
    async with db.session() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM procurements WHERE customer_id IS NOT NULL "
                "AND number IN ('L-1','L-2')"
            )
        )
        assert result.scalar_one() == 2
