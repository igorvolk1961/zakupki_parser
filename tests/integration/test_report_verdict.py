"""Интеграционные тесты единого отчёта (FR-13.1/13.3): авто-отклонение/
восстановление и гейт кнопки «Анализ» (``analysis_stale``).

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
async def test_auto_rejected_sets_status_and_does_not_clobber_manual(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "verdict-a", keywords=["ИИ"])
    pid = await _upsert(repo, "VERDICT-1")
    await repo.record_matched_keywords(pid, profile_id, ["ИИ"])

    # Вердикт анализа: блокирующее условие сработало -> авто-отклонение.
    await repo.update_rag_report(
        pid,
        profile_id,
        {"verdict": {"accepted": False}},
        auto_rejected=True,
        auto_rejection_reason="Авто: Лицензии",
    )
    evaluation = await repo.get_score(pid, profile_id)
    assert evaluation is not None
    assert evaluation.status == "rejected"
    assert evaluation.auto_rejected is True
    assert evaluation.rejection_reason == "Авто: Лицензии"

    # Повторный анализ с «неблокирующим» вердиктом снимает СВОЁ авто-отклонение.
    await repo.update_rag_report(
        pid, profile_id, {"verdict": {"accepted": True}}, auto_rejected=False
    )
    evaluation = await repo.get_score(pid, profile_id)
    assert evaluation is not None
    assert evaluation.status == "new"
    assert evaluation.auto_rejected is False
    assert evaluation.rejection_reason is None

    # Ручная отбраковка НЕ снимается повторным «неблокирующим» анализом.
    await repo.reject(pid, profile_id, rejection_reason="не наш профиль")
    await repo.update_rag_report(
        pid, profile_id, {"verdict": {"accepted": True}}, auto_rejected=False
    )
    evaluation = await repo.get_score(pid, profile_id)
    assert evaluation is not None
    assert evaluation.status == "rejected"
    assert evaluation.rejection_reason == "не наш профиль"
    assert evaluation.auto_rejected is False

    # А вот авто-отклонение НЕ перезаписывает уже стоящую ручную отбраковку
    # (проверяем отдельно, с чистого состояния).
    pid2 = await _upsert(repo, "VERDICT-2")
    await repo.reject(pid2, profile_id, rejection_reason="ручная причина")
    await repo.update_rag_report(
        pid2,
        profile_id,
        {"verdict": {"accepted": False}},
        auto_rejected=True,
        auto_rejection_reason="Авто: Опыт",
    )
    evaluation2 = await repo.get_score(pid2, profile_id)
    assert evaluation2 is not None
    assert evaluation2.rejection_reason == "ручная причина"
    assert evaluation2.auto_rejected is False


@pytest.mark.slow
@pytest.mark.asyncio
async def test_restore_clears_manual_and_auto_rejection(db: Database) -> None:
    repo = ProcurementRepository(db)
    _, profile_id = await _profile_with_keywords(repo, "verdict-restore", keywords=["ИИ"])
    pid = await _upsert(repo, "VERDICT-3")
    await repo.record_matched_keywords(pid, profile_id, ["ИИ"])

    await repo.reject(pid, profile_id, rejection_reason="причина")
    restored = await repo.restore(pid, profile_id)
    assert restored is not None
    assert restored.status == "new"
    assert restored.rejection_reason is None
    assert restored.auto_rejected is False

    # Несуществующая пара (закупка, профиль) — None (нечего восстанавливать).
    other_pid = await _upsert(repo, "VERDICT-4")
    assert await repo.restore(other_pid, profile_id) is None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_analysis_stale_gate(db: Database) -> None:
    """Кнопка «Анализ» (``analysis_stale``): актуален только пока профиль
    не менялся с последнего анализа (FR-13.3)."""
    repo = ProcurementRepository(db)
    user_id, profile_id = await _profile_with_keywords(repo, "verdict-stale", keywords=["ИИ"])
    pid = await _upsert(repo, "VERDICT-5")
    await repo.record_matched_keywords(pid, profile_id, ["ИИ"])

    # Анализа ещё не было — стейл по умолчанию (кнопка активна).
    row = await repo.get_by_id(pid, profile_id=profile_id)
    assert row is not None
    assert row.analysis_stale is True

    # Снимок профиля на момент анализа — profiles.updated_at СЕЙЧАС.
    profile_row = await repo.get_profile_by_id(profile_id)
    assert profile_row is not None
    snapshot_at = profile_row.updated_at

    await repo.update_rag_report(
        pid, profile_id, {"status": "ok"}, analysis_profile_snapshot=snapshot_at
    )
    row = await repo.get_by_id(pid, profile_id=profile_id)
    assert row is not None
    assert row.analysis_stale is False  # анализ актуален -> кнопка disabled

    # То же должно быть верно и в list_procurements (не только get_by_id).
    rows, _ = await repo.list_procurements(profile_id=profile_id)
    assert rows[0].analysis_stale is False

    # Профиль изменился -> анализ снова считается устаревшим.
    await repo.upsert_profile({"name": "default", "enabled": False}, user_id, profile_id=profile_id)
    row = await repo.get_by_id(pid, profile_id=profile_id)
    assert row is not None
    assert row.analysis_stale is True
