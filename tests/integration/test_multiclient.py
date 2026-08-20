"""Интеграционные тесты многоклиентного скоринга (требуют PostgreSQL).

Проверяют: CRUD клиентских профилей, per-client скоринг через POST /score,
rag_report, ручные оценки manual/reject, on-demand analyze/pwin-margin.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")


@pytest.fixture(scope="module")
def mc_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    async def _setup() -> None:
        engine = create_async_engine(TEST_DSN)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert_client(
                {
                    "name": "default",
                    "enabled": True,
                    "competencies": "Тестовые компетенции",
                    "keywords": [],
                    "exclusion_words": ["медицинский"],
                    "keyword_context_regexes": {},
                    "questions": [{"id": "q1", "text": "Требуется ли лицензия?"}],
                }
            )
        finally:
            await db.dispose()

    asyncio.run(_setup())
    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    app = create_app()
    with TestClient(app) as client:
        yield client
    os.environ.pop("ZAKUPKI_DB_DSN", None)


def _seed_procurement() -> int:
    async def _seed() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {"number": "MC-1", "platform_id": "zakupki_mos", "subject": "Разработка ИИ"}
            )
            rows, _ = await repo.list_procurements(number="MC-1")
            return rows[0].id
        finally:
            await db.dispose()

    return asyncio.run(_seed())


def test_clients_crud(mc_client: TestClient) -> None:
    client = mc_client
    active = client.get("/api/clients/active")
    assert active.status_code == 200
    assert active.json()["name"] == "default"
    assert active.json()["exclusion_words"] == ["медицинский"]
    assert active.json()["questions"] == [{"id": "q1", "text": "Требуется ли лицензия?"}]

    # Создание профиля (POST /api/clients — upsert по name).
    created = client.post(
        "/api/clients",
        json={"name": "client-b", "competencies": "Компетенции B", "keywords": ["ИИ"]},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "client-b"
    assert body["keywords"] == ["ИИ"]

    listed = client.get("/api/clients")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 2


def test_manual_score_and_reject(mc_client: TestClient) -> None:
    client = mc_client
    procurement_id = _seed_procurement()

    r = client.post(f"/api/procurements/{procurement_id}/manual-score", json={"value": 0.8})
    assert r.status_code == 200
    card = r.json()
    assert card["score_method"] == "manual"
    assert card["fit_score"] == 0.8
    assert card["score"] == pytest.approx(0.8)  # p_win/margin отсутствуют -> 1.0

    # Недопустимый пресет — 422.
    assert (
        client.post(f"/api/procurements/{procurement_id}/manual-score", json={"value": 0.55})
    ).status_code == 422

    r = client.post(f"/api/procurements/{procurement_id}/reject")
    assert r.status_code == 200
    card = r.json()
    assert card["score_method"] == "reject"
    assert card["fit_score"] == 0.1


def test_rag_report_via_score_endpoint(mc_client: TestClient) -> None:
    client = mc_client
    procurement_id = _seed_procurement()
    report = {
        "tz_found": True,
        "tz_file": "ТЗ.docx",
        "questions": [
            {
                "question_id": "q1",
                "question_text": "Требуется ли лицензия?",
                "verdict": "absolute",
                "severity": 2,
                "excerpt": "Лицензия обязательна",
                "reasoning": "жёсткое требование",
            }
        ],
        "generated_at": "2026-08-19T00:00:00+00:00",
    }
    r = client.post(
        f"/api/procurements/{procurement_id}/score",
        json={"score": 10.0, "fit_score": 0.7, "score_method": "fit", "rag_report": report},
    )
    assert r.status_code == 200
    card = r.json()
    assert card["rag_report"]["tz_found"] is True
    assert card["rag_report"]["questions"][0]["verdict"] == "absolute"
    # rag_report не меняет score_method.
    assert card["score_method"] == "fit"


def test_analyze_and_pwin_margin_queue(mc_client: TestClient) -> None:
    """Эндпоинты on-demand требуют настроенного транспорта (409 в тестах)."""
    client = mc_client
    procurement_id = _seed_procurement()
    # В тестовом конфиге scoring_transport_url не задан -> 409.
    r = client.post("/api/procurements/analyze", json={"procurement_ids": [procurement_id]})
    assert r.status_code == 409
    r = client.post("/api/procurements/pwin-margin", json={"procurement_ids": [procurement_id]})
    assert r.status_code == 409


def test_list_uses_active_client_scores(mc_client: TestClient) -> None:
    client = mc_client
    procurement_id = _seed_procurement()
    client.post(f"/api/procurements/{procurement_id}/manual-score", json={"value": 0.9})
    data = client.get("/api/procurements").json()
    item = next((i for i in data["items"] if i["id"] == procurement_id), None)
    assert item is not None
    assert item["fit_score"] == 0.9
    assert item["score_method"] == "manual"
