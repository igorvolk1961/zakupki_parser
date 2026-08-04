"""Интеграционные тесты репозитория БД (требуют PostgreSQL).

Тесты запускаются, если задан DSN в переменной окружения ``ZAKUPKI_TEST_DSN``
(например, ``postgresql+asyncpg://postgres:postgres@localhost:5432/zakupki_test``).
В противном случае тесты пропускаются (skip).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

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


@pytest.mark.asyncio
async def test_upsert_creates(db: Database) -> None:
    repo = ProcurementRepository(db)
    deadline = datetime(2026, 8, 6, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    ok = await repo.upsert(
        {
            "number": "ABC-1",
            "source_platform": "zakupki_mos",
            "subject": "Тест",
            "url": "https://example.com/need/1",
            "deadline": deadline,
            "detail_json": {"deadline": deadline.isoformat(), "nested": [1, 2]},
        }
    )
    assert ok is True
    assert await repo.exists("ABC-1", "zakupki_mos") is True


@pytest.mark.asyncio
async def test_upsert_no_duplicate(db: Database) -> None:
    repo = ProcurementRepository(db)
    record = {
        "number": "ABC-2",
        "source_platform": "zakupki_mos",
        "subject": "Тест 2",
    }
    first = await repo.upsert(record)
    second = await repo.upsert(record)
    assert first is True
    assert second is False  # повторная запись того же номера исключена


@pytest.mark.asyncio
async def test_exists_false_for_unknown(db: Database) -> None:
    repo = ProcurementRepository(db)
    assert await repo.exists("NOPE", "zakupki_mos") is False


@pytest.mark.asyncio
async def test_count_one(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "ABC-3", "source_platform": "zakupki_mos", "subject": "x"})
    async with db.session() as session:
        result = await session.execute(text("SELECT count(*) FROM procurements"))
        count = result.scalar_one()
    assert count == 1
