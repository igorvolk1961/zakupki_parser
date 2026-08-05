"""Интеграционные тесты FastAPI-сервиса (требуют PostgreSQL)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.object_store import LocalObjectStore
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")


@pytest.fixture(scope="module")
def api_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[TestClient, Path]]:
    async def _setup() -> None:
        engine = create_async_engine(TEST_DSN)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup())
    docs = tmp_path_factory.mktemp("docs")

    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    app = create_app()
    app.state.parser.store = LocalObjectStore(docs)
    app.state.parser.cfg.service.storage.type = "local"
    with TestClient(app) as client:
        yield client, docs
    os.environ.pop("ZAKUPKI_DB_DSN", None)


@pytest.fixture(scope="module")
async def inserted_id(api_client: tuple[TestClient, Path]) -> AsyncIterator[int]:
    client, _ = api_client
    db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
    await db.connect()
    repo = ProcurementRepository(db)
    await repo.upsert(
        {
            "number": "API-1",
            "source_platform": "zakupki_mos",
            "subject": "Тест API",
            "customer": "Заказчик ООО",
            "okpd2_codes": "62.01",
            "technical_spec_url": str((api_client[1] / "API-1" / "ТЗ.pdf").resolve()),
            "technical_spec_key": "API-1/ТЗ.pdf",
        }
    )
    rows, _ = await repo.list(number="API-1")
    await db.dispose()
    yield rows[0].id


def test_health(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["storage"] == "local"


def test_list_and_get(api_client: tuple[TestClient, Path], inserted_id: int) -> None:
    client, _ = api_client
    resp = client.get("/api/procurements", params={"number": "API-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["id"] == inserted_id for item in body["items"])

    detail = client.get(f"/api/procurements/{inserted_id}")
    assert detail.status_code == 200
    assert detail.json()["number"] == "API-1"


def test_missing_procurement_404(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    assert client.get("/api/procurements/999999").status_code == 404


def test_technical_spec_download(api_client: tuple[TestClient, Path], inserted_id: int) -> None:
    client, docs = api_client
    (docs / "API-1").mkdir(parents=True, exist_ok=True)
    (docs / "API-1" / "ТЗ.pdf").write_bytes(b"%PDF-fake")
    resp = client.get(f"/api/procurements/{inserted_id}/technical-spec")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-fake"
    assert "attachment" in resp.headers["content-disposition"]
