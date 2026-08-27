"""Интеграционные тесты доступа к статусу парсера (требуют PostgreSQL).

Проверяют, что GET /api/parser/status доступен аккаунту без роли devops
(статус нужен всем), а управление парсером (POST /api/parser/start) остаётся
devops-only (403).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.auth import hash_password
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")

_SECRET = "status-access-secret"


@pytest.fixture(scope="module")
def limited_user_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Приложение с авторизацией и аккаунтом только с ролью 'user' (не devops)."""

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
            await repo.create_user(
                "status_user",
                await asyncio.to_thread(hash_password, "userpass"),
                ["user"],
            )
        finally:
            await db.dispose()

    asyncio.run(_setup())
    cfgdir = tmp_path_factory.mktemp("configs_status")
    src = Path(__file__).resolve().parents[2] / "tests" / "configs"
    shutil.copytree(src, cfgdir, dirs_exist_ok=True)
    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    os.environ["ZAKUPKI_AUTH_ENABLED"] = "true"
    os.environ["ZAKUPKI_AUTH_SECRET"] = _SECRET
    os.environ["ZAKUPKI_INTERNAL_TOKEN"] = "internal-secret"
    app = create_app(str(cfgdir))
    with TestClient(app) as client:
        yield client
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_AUTH_ENABLED", None)
    os.environ.pop("ZAKUPKI_AUTH_SECRET", None)
    os.environ.pop("ZAKUPKI_INTERNAL_TOKEN", None)


def _limited_auth(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": "status_user", "password": "userpass"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_parser_status_allowed_for_non_devops(limited_user_client: TestClient) -> None:
    """Аккаунт без роли devops читает статус парсера (200), а не получает 403."""
    client = limited_user_client
    resp = client.get("/api/parser/status", headers=_limited_auth(client))
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["error"] is None


def test_parser_start_still_devops_only(limited_user_client: TestClient) -> None:
    """Управление парсером остаётся закрытым для ролей без devops."""
    client = limited_user_client
    resp = client.post("/api/parser/start", headers=_limited_auth(client))
    assert resp.status_code == 403
