"""Интеграционные тесты репозитория БД (требуют PostgreSQL).

Тесты запускаются, если задан DSN в переменной окружения ``ZAKUPKI_TEST_DSN``
(например, ``postgresql+asyncpg://postgres:postgres@localhost:5432/zakupki_test``).
В противном случае тесты пропускаются (skip).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone

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
async def test_known_numbers(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "KN-1", "source_platform": "zakupki_mos", "subject": "x"})
    await repo.upsert({"number": "KN-2", "source_platform": "zakupki_mos", "subject": "y"})
    await repo.upsert({"number": "OTHER-1", "source_platform": "fabrikant", "subject": "z"})
    assert await repo.known_numbers("zakupki_mos") == {"KN-1", "KN-2"}


@pytest.mark.asyncio
async def test_count(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "C-1", "source_platform": "zakupki_mos", "subject": "x"})
    await repo.upsert({"number": "C-2", "source_platform": "zakupki_mos", "subject": "y"})
    await repo.upsert({"number": "C-3", "source_platform": "fabrikant", "subject": "z"})
    assert await repo.count("zakupki_mos") == 2
    assert await repo.count() == 3


@pytest.mark.asyncio
async def test_count_one(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert({"number": "ABC-3", "source_platform": "zakupki_mos", "subject": "x"})
    async with db.session() as session:
        result = await session.execute(text("SELECT count(*) FROM procurements"))
        count = result.scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_last_processed_date(db: Database) -> None:
    repo = ProcurementRepository(db)
    await repo.upsert(
        {
            "number": "ABC-4",
            "source_platform": "zakupki_mos",
            "subject": "x",
            "update_date": datetime(2026, 8, 4, 0, 0, tzinfo=timezone(timedelta(hours=3))),
        }
    )
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    cutoff = await repo.last_processed_date("zakupki_mos", now, default_cutoff_days=7)
    assert cutoff == datetime(2026, 8, 3, 21, 0, tzinfo=UTC)

    unknown = await repo.last_processed_date("nope", now, default_cutoff_days=7)
    assert unknown == now - timedelta(days=7)


@pytest.mark.asyncio
async def test_is_active_default_and_upsert(db: Database) -> None:
    repo = ProcurementRepository(db)
    # Без явного is_active — по умолчанию активна.
    await repo.upsert({"number": "ABC-5", "source_platform": "zakupki_mos", "subject": "x"})
    # Явная неактивная закупка.
    await repo.upsert(
        {
            "number": "ABC-6",
            "source_platform": "zakupki_mos",
            "subject": "y",
            "is_active": False,
        }
    )
    active, _ = await repo.list_procurements(active=True)
    inactive, _ = await repo.list_procurements(active=False)
    assert any(p.number == "ABC-5" for p in active)
    assert all(p.number != "ABC-5" for p in inactive)
    assert any(p.number == "ABC-6" for p in inactive)
    assert all(p.number != "ABC-6" for p in active)


@pytest.mark.asyncio
async def test_list_active_considers_deadline(db: Database) -> None:
    """Фильтр active на стороне клиента учитывает срок актуальности.

    is_active в БД — только статус; истёкший дедлайн делает закупку неактивной
    при чтении (active фильтр/эффективная активность).
    """
    repo = ProcurementRepository(db)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    # Активный статус, но срок истёк — в БД is_active=true, клиент считает неактивной.
    await repo.upsert(
        {
            "number": "DL-1",
            "source_platform": "zakupki_mos",
            "subject": "x",
            "is_active": True,
            "deadline": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        }
    )
    # Активный статус и будущий дедлайн — активна.
    await repo.upsert(
        {
            "number": "DL-2",
            "source_platform": "zakupki_mos",
            "subject": "y",
            "is_active": True,
            "deadline": datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        }
    )
    # Неактивный статус, дедлайн в будущем — неактивна.
    await repo.upsert(
        {
            "number": "DL-3",
            "source_platform": "zakupki_mos",
            "subject": "z",
            "is_active": False,
            "deadline": datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        }
    )
    active, _ = await repo.list_procurements(active=True, now=now)
    inactive, _ = await repo.list_procurements(active=False, now=now)
    assert {p.number for p in active} == {"DL-2"}
    assert {p.number for p in inactive} == {"DL-1", "DL-3"}


@pytest.mark.asyncio
async def test_delete_inactive(db: Database) -> None:
    """delete_inactive удаляет закупки с неактивным статусом или истёкшим сроком."""
    repo = ProcurementRepository(db)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    # Неактивна по статусу (is_active=false в БД).
    await repo.upsert(
        {"number": "DI-1", "source_platform": "zakupki_mos", "subject": "x", "is_active": False}
    )
    # Активна по статусу, но срок истёк — клиент считает неактивной.
    await repo.upsert(
        {
            "number": "DI-2",
            "source_platform": "zakupki_mos",
            "subject": "y",
            "is_active": True,
            "deadline": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        }
    )
    # Активна по статусу и срок не истёк.
    await repo.upsert(
        {
            "number": "DI-3",
            "source_platform": "zakupki_mos",
            "subject": "z",
            "is_active": True,
            "deadline": datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        }
    )
    deleted = await repo.delete_inactive(now=now)
    assert deleted == 2
    rows, _ = await repo.list_procurements()
    assert {p.number for p in rows} == {"DI-3"}


@pytest.mark.asyncio
async def test_delete_irrelevant(db: Database) -> None:
    """delete_irrelevant удаляет только обработанные скорингом записи с fit_score < порога."""
    repo = ProcurementRepository(db)
    # Внешний скоринг, fit_score >= порога — релевантна, остаётся.
    await repo.upsert(
        {
            "number": "RI-1",
            "source_platform": "zakupki_mos",
            "subject": "x",
            "fit_score": 0.8,
            "score_method": "external",
        }
    )
    # Внешний скоринг, fit_score ниже порога — нерелевантна, удаляется.
    await repo.upsert(
        {
            "number": "RI-2",
            "source_platform": "zakupki_mos",
            "subject": "y",
            "fit_score": 0.2,
            "score_method": "external",
        }
    )
    # Дефолтный скоринг (внешний не проходил) — НЕ учитывается, остаётся.
    await repo.upsert(
        {
            "number": "RI-3",
            "source_platform": "zakupki_mos",
            "subject": "z",
            "fit_score": 0.1,
            "score_method": "default",
        }
    )
    deleted = await repo.delete_irrelevant(min_fit_score=0.4)
    assert deleted == 1
    rows, _ = await repo.list_procurements()
    assert {p.number for p in rows} == {"RI-1", "RI-3"}
