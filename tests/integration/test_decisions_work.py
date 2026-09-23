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


@pytest.mark.slow
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


@pytest.mark.slow
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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_add_exclusion_word_does_not_reject_procurement(db: Database) -> None:
    """add_exclusion_word (карточка -> «В исключения») добавляет слово без
    отбраковки закупки — в отличие от reject(exclusion_word=...)."""
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "excl-only", keywords=["ИИ"])
    pid = await _upsert(repo, "EXCL-1")
    await repo.record_matched_keywords(pid, profile_id, ["ИИ"])

    added = await repo.add_exclusion_word(profile_id, "не наш профиль*")
    assert added is True
    words = await repo.get_profile_keywords(profile_id)
    assert "не наш профиль*" in words["exclusion_words"]

    evaluation = await repo.get_score(pid, profile_id)
    assert evaluation is not None and evaluation.status != "rejected"

    # Повторное добавление того же слова — идемпотентно (added=False, не дублирует).
    added_again = await repo.add_exclusion_word(profile_id, "не наш профиль*")
    assert added_again is False
    words2 = await repo.get_profile_keywords(profile_id)
    assert words2["exclusion_words"].count("не наш профиль*") == 1


@pytest.mark.slow
@pytest.mark.asyncio
async def test_set_in_work_flag_and_list(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "work-user")
    pid = await _upsert(repo, "WORK-1")

    assert await repo.set_in_work(pid, True) is True

    rows, total = await repo.list_procurements(profile_id=profile_id)
    assert total == 1
    assert rows[0].in_work is True

    # Повторная установка идемпотентна.
    assert await repo.set_in_work(pid, True) is True

    # Снятие с работы: колонка снята. Закупка не отобрана профилем никак
    # иначе (нет записи оценки/matched_keywords) — из ЭТОГО профильного
    # списка она поэтому и пропадает (BR-07, ожидаемо, не баг).
    assert await repo.set_in_work(pid, False) is True
    row = await repo.get_by_id(pid)
    assert row is not None and row.in_work is False
    rows, total = await repo.list_procurements(profile_id=profile_id)
    assert total == 0

    # Несуществующая закупка — False.
    assert await repo.set_in_work(10**9, True) is False


@pytest.mark.slow
@pytest.mark.asyncio
async def test_clear_all_keeps_in_work_unless_requested(db: Database) -> None:
    repo = ProcurementRepository(db)
    pid = await _upsert(repo, "WORK-CL", subject="Сохранить в работе")
    await _upsert(repo, "WORK-CL-2", subject="Обычная")
    await repo.set_in_work(pid, True)

    # Очистка без include_in_work: обычная закупка удалена, «в работе» — нет.
    deleted = await repo.clear_all()
    assert deleted["procurements"] == 1
    assert deleted["work_items"] == 0
    assert await repo.find_id("WORK-CL", "zakupki_mos") == pid
    assert await repo.find_id("WORK-CL-2", "zakupki_mos") is None

    # Явная очистка «в работе» удаляет и её тоже.
    deleted = await repo.clear_all(include_in_work=True)
    assert deleted["procurements"] == 1
    assert deleted["work_items"] == 1
    assert await repo.find_id("WORK-CL", "zakupki_mos") is None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_list_in_work_filter(db: Database) -> None:
    """Единый список: фильтр in_work возвращает закупки, принятые «в работу»."""
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "work-filter")
    in_work_id = await _upsert(repo, "WORK-F1")
    other_id = await _upsert(repo, "WORK-F2")
    await repo.record_matched_keywords(in_work_id, profile_id, ["слово"])
    await repo.record_matched_keywords(other_id, profile_id, ["слово"])
    await repo.set_in_work(in_work_id, True)

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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_in_work_is_global_across_profiles(db: Database) -> None:
    """Признак «в работе» общий для закупки, не per-profile (в отличие от прежней
    таблицы procurement_work_items): закупка, добавленная «в работу», видна в
    выдаче любого профиля, даже если он её не отбирал (BR-07 доп. к score_sub)."""
    repo = ProcurementRepository(db)
    _, profile_a = await _profile_with_keywords(repo, "work-a")
    _, profile_b = await _profile_with_keywords(repo, "work-b")
    pid = await _upsert(repo, "WORK-PP")

    await repo.set_in_work(pid, True)

    rows_a, _ = await repo.list_procurements(profile_id=profile_a)
    rows_b, _ = await repo.list_procurements(profile_id=profile_b)
    assert [r.id for r in rows_a] == [pid]
    assert [r.id for r in rows_b] == [pid]
    assert rows_a[0].in_work is True and rows_b[0].in_work is True
