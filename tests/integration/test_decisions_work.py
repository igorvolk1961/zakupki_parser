"""Интеграционные тесты Эпика 5: отбраковка и «в работе» (требуют PostgreSQL).

Тесты запускаются, если задан DSN в переменной окружения ``ZAKUPKI_TEST_DSN``.
В противном случае тесты пропускаются (skip).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

COMP_JSON = json.dumps(
    {
        "positioning": "Тестовые компетенции",
        "breadth": "broad",
        "competencies": [{"area": "Аудит", "description": "обследование"}],
        "exclusions": [],
    },
    ensure_ascii=False,
    separators=(",", ":"),
)

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


async def _profile_with_keywords(
    repo: ProcurementRepository, username: str, keywords: list[str] | None = None
) -> tuple[int, int]:
    """Создаёт пользователя и его профиль; возвращает (user_id, profile_id)."""
    user = await repo.create_user(username, "hash", ["user"])
    profile = await repo.upsert_profile(
        {
            "name": "default",
            "competencies": COMP_JSON,
            "keywords": keywords or [],
            "exclusion_words": [],
        },
        user.id,
    )
    assert profile.id is not None
    return user.id, profile.id


async def _upsert(repo: ProcurementRepository, number: str, **extra: object) -> int:
    ok = await repo.upsert(
        {"number": number, "platform_id": "zakupki_mos", "subject": "x", **extra}
    )
    assert ok is True
    rows, _ = await repo.list_procurements(number=number)
    return next(p.id for p in rows if p.number == number)


@pytest.mark.asyncio
async def test_reject_sets_status_and_hides_from_list(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "rej-user", keywords=["ИИ"])
    pid = await _upsert(repo, "REJ-1")
    await repo.record_matched_keywords(pid, profile_id, ["ИИ"])

    rows, total = await repo.list_procurements(profile_id=profile_id)
    assert total == 1 and rows[0].id == pid

    await repo.reject(pid, profile_id, rejection_reason="не наш профиль")
    evaluation = await repo.get_score(pid, profile_id)
    assert evaluation is not None
    assert evaluation.status == "rejected"
    assert evaluation.rejection_reason == "не наш профиль"

    # Отклонённая скрыта из выдачи; с include_rejected=True — видна.
    rows, total = await repo.list_procurements(profile_id=profile_id)
    assert total == 0 and rows == []
    rows, total = await repo.list_procurements(include_rejected=True, profile_id=profile_id)
    assert total == 1 and rows[0].id == pid


@pytest.mark.asyncio
async def test_reject_removes_matched_keywords(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "rej-kw", keywords=["ИИ", "роботы"])
    pid = await _upsert(repo, "REJ-2")
    await repo.record_matched_keywords(pid, profile_id, ["ИИ"])

    await repo.reject(pid, profile_id, remove_matched_keywords=True)
    words = await repo.get_profile_keywords(profile_id)
    assert words["keywords"] == ["роботы"]  # «ИИ» убран из профиля
    evaluation = await repo.get_score(pid, profile_id)
    assert evaluation is not None and evaluation.status == "rejected"


@pytest.mark.asyncio
async def test_reject_adds_exclusion_word(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "rej-excl", keywords=["ИИ"])
    pid = await _upsert(repo, "REJ-3")
    await repo.record_matched_keywords(pid, profile_id, ["ИИ"])

    await repo.reject(pid, profile_id, exclusion_word="медицина")
    words = await repo.get_profile_keywords(profile_id)
    assert words["exclusion_words"] == ["медицина"]
    # Повторное добавление того же исключения — идемпотентно (unique).
    await repo.reject(pid, profile_id, exclusion_word="медицина")
    words = await repo.get_profile_keywords(profile_id)
    assert words["exclusion_words"] == ["медицина"]


@pytest.mark.asyncio
async def test_accept_into_work_flag_and_list(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "work-user")
    pid = await _upsert(repo, "WORK-1")

    item = await repo.accept_into_work(pid, profile_id)
    assert item is not None
    assert item.procurement_id == pid
    assert item.source == "search"

    rows, total = await repo.list_procurements(profile_id=profile_id)
    assert total == 1
    assert rows[0].in_work is True

    items = await repo.list_work_items(profile_id)
    assert [i.procurement_id for i in items] == [pid]

    # Повторное принятие идемпотентно: запись одна.
    await repo.accept_into_work(pid, profile_id)
    assert len(await repo.list_work_items(profile_id)) == 1

    # Снятие с работы удаляет только запись; закупка остаётся в выдаче.
    assert await repo.remove_from_work(profile_id, pid) is True
    assert await repo.list_work_items(profile_id) == []
    rows, total = await repo.list_procurements(profile_id=profile_id)
    assert total == 1 and rows[0].id == pid
    assert rows[0].in_work is False


@pytest.mark.asyncio
async def test_accept_by_url_existing_and_snapshot(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "work-url")
    pid = await _upsert(repo, "WORK-2", url="https://zakupki.example.com/need/2", subject="По URL")

    # Закупка с таким URL уже есть — привязываемся к ней (source='url').
    item = await repo.accept_into_work_by_url("https://zakupki.example.com/need/2", profile_id)
    assert item.procurement_id == pid
    assert item.source == "url"
    assert item.number == "WORK-2"

    # Неизвестный URL — создаётся запись-снимок (procurement_id IS NULL).
    snapshot = await repo.accept_into_work_by_url(
        "https://etp.example.com/purchase/999", profile_id, notes="проверить"
    )
    assert snapshot.procurement_id is None
    assert snapshot.url == "https://etp.example.com/purchase/999"
    assert snapshot.notes == "проверить"
    assert snapshot.status == "in_work"

    # Снимок удаляется по id записи «в работе» (профильный скоуп BR-07) —
    # привязанная к закупке запись остаётся.
    assert await repo.remove_work_item(profile_id, snapshot.id) is True
    items = await repo.list_work_items(profile_id)
    assert len(items) == 1
    assert items[0].procurement_id == pid


@pytest.mark.asyncio
async def test_clear_all_keeps_work_items_unless_requested(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "work-clear")
    pid = await _upsert(repo, "WORK-CL", subject="Сохранить в работе")
    await repo.accept_into_work(pid, profile_id)

    # Очистка без include_work_items: procurement удалён, запись «в работе» живёт
    # (снимок в самой записи, procurement_id обнулён FK SET NULL).
    deleted = await repo.clear_all()
    assert deleted["procurements"] == 1
    items = await repo.list_work_items(profile_id)
    assert len(items) == 1
    assert items[0].procurement_id is None
    assert items[0].subject == "Сохранить в работе"

    # Явная очистка «в работе» удаляет и записи тоже.
    deleted = await repo.clear_all(include_work_items=True)
    assert deleted["work_items"] == 1
    assert await repo.list_work_items(profile_id) == []


@pytest.mark.asyncio
async def test_list_in_work_filter(db: Database) -> None:
    """Единый список: фильтр in_work возвращает закупки профиля «в работе»."""
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "work-filter")
    in_work_id = await _upsert(repo, "WORK-F1")
    other_id = await _upsert(repo, "WORK-F2")
    await repo.accept_into_work(in_work_id, profile_id)

    rows, total = await repo.list_procurements(profile_id=profile_id, in_work=True)
    assert total == 1
    assert [r.id for r in rows] == [in_work_id]
    assert rows[0].in_work is True

    rows, total = await repo.list_procurements(profile_id=profile_id)
    assert total == 2
    by_id = {r.id: r.in_work for r in rows}
    assert by_id[in_work_id] is True and by_id[other_id] is False

    # Карточка закупки (get_by_id) тоже отдаёт признак «в работе».
    row = await repo.get_by_id(in_work_id, profile_id=profile_id)
    assert row is not None and row.in_work is True
    row_other = await repo.get_by_id(other_id, profile_id=profile_id)
    assert row_other is not None and row_other.in_work is False


@pytest.mark.asyncio
async def test_work_is_per_profile(db: Database) -> None:
    """Признак «в работе» изолирован по профилю (BR-07)."""
    repo = ProcurementRepository(db)
    _, profile_a = await _profile_with_keywords(repo, "work-a")
    _, profile_b = await _profile_with_keywords(repo, "work-b")
    pid = await _upsert(repo, "WORK-PP")

    await repo.accept_into_work(pid, profile_a)

    # Другой профиль этого не видит.
    rows, _ = await repo.list_procurements(profile_id=profile_b)
    assert rows[0].in_work is False
    assert await repo.list_work_items(profile_b) == []
    assert len(await repo.list_work_items(profile_a)) == 1
