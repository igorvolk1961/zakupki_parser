"""Интеграционные тесты авторизации (требуют PostgreSQL).

Проверяют самостоятельную регистрацию (пароль + подтверждение), вход и защиту
эндпоинтов. Роль при регистрации всегда ``tenderologist`` — администраторская
роль регистрацией не выдаётся.
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
from starlette.websockets import WebSocketDisconnect

from zakupki_parser.api.app import create_app
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")

SECRET = "integration-test-secret"


@pytest.fixture(scope="module")
def auth_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Приложение с включённой авторизацией и копией тестовых конфигов."""

    async def _setup() -> None:
        engine = create_async_engine(TEST_DSN)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()
        # Сид сервис-аккаунта и default-профиля под ним (в проде — миграция 1.27/1.29
        # и ensure_service_account на старте).
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            user = await repo.first_user()
            if user is None:
                user = await repo.create_user("admin", "test-hash", "admin")
            await repo.upsert_profile(
                {
                    "name": "default",
                    "enabled": True,
                    "is_active": True,
                    "competencies": "Тестовые компетенции",
                    "keywords": [],
                    "exclusion_words": [],
                    "questions": [],
                },
                user.id,
            )
        finally:
            await db.dispose()

    asyncio.run(_setup())

    cfgdir = tmp_path_factory.mktemp("configs_auth")
    src = Path(__file__).resolve().parents[2] / "tests" / "configs"
    shutil.copytree(src, cfgdir, dirs_exist_ok=True)
    ops = cfgdir / "config_ops.yaml"
    ops.write_text(
        ops.read_text(encoding="utf-8") + "\nauth:\n  enabled: true\n  token_ttl_seconds: 3600\n",
        encoding="utf-8",
    )

    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    os.environ["ZAKUPKI_AUTH_SECRET"] = SECRET
    os.environ["ZAKUPKI_INTERNAL_TOKEN"] = "internal-secret"
    os.environ.pop("ZAKUPKI_ADMIN_USERNAME", None)
    os.environ.pop("ZAKUPKI_ADMIN_PASSWORD", None)
    app = create_app(str(cfgdir))
    with TestClient(app) as client:
        yield client
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_AUTH_SECRET", None)
    os.environ.pop("ZAKUPKI_INTERNAL_TOKEN", None)


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_login_me(auth_client: TestClient) -> None:
    client = auth_client
    # Регистрация: пароль выбирает сам пользователь + подтверждение, роль — tenderologist.
    resp = client.post(
        "/api/auth/register",
        json={"username": "tender1", "password": "password123", "password_confirm": "password123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["username"] == "tender1"
    assert body["user"]["role"] == "tenderologist"

    # Дубликат логина отклоняется.
    dup = client.post(
        "/api/auth/register",
        json={"username": "tender1", "password": "password123", "password_confirm": "password123"},
    )
    assert dup.status_code == 409

    # Подтверждение пароля не совпадает — 422.
    mismatch = client.post(
        "/api/auth/register",
        json={"username": "tender2", "password": "password123", "password_confirm": "password124"},
    )
    assert mismatch.status_code == 422

    # Короткий пароль — 422.
    short = client.post(
        "/api/auth/register",
        json={"username": "short", "password": "short", "password_confirm": "short"},
    )
    assert short.status_code == 422

    # Вход под тем же паролем, который выбрал пользователь.
    token = _login(client, "tender1", "password123")
    me = client.get("/api/auth/me", headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["username"] == "tender1"

    # Выход (stateless) и неверный пароль.
    assert client.post("/api/auth/logout", headers=_headers(token)).status_code == 200
    assert (
        client.post(
            "/api/auth/login", json={"username": "tender1", "password": "wrong"}
        ).status_code
        == 401
    )


def test_public_endpoints_open(auth_client: TestClient) -> None:
    client = auth_client
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_pipeline_endpoints_require_internal_token(auth_client: TestClient) -> None:
    """Служебные эндпоинты конвейера (POST /score) закрыты внутренним токеном."""
    client = auth_client

    async def _seed() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            assert await repo.upsert(
                {"number": "AUTH-SCORE", "platform_id": "zakupki_mos", "subject": "Токен"}
            )
            rows, _ = await repo.list_procurements(number="AUTH-SCORE")
            return rows[0].id
        finally:
            await db.dispose()

    procurement_id = asyncio.run(_seed())

    # Без внутреннего токена — 401 (даже для анонима).
    resp = client.post(
        f"/api/procurements/{procurement_id}/score",
        json={"score": 10.0, "fit_score": 0.9, "score_method": "fit"},
    )
    assert resp.status_code == 401

    # С корректным внутренним токеном — работает (вызывается конвейером).
    ok = client.post(
        f"/api/procurements/{procurement_id}/score",
        json={"score": 10.0, "fit_score": 0.9, "score_method": "fit"},
        headers={"X-Internal-Token": "internal-secret"},
    )
    assert ok.status_code == 200

    # С неверным токеном — 401.
    bad = client.post(
        f"/api/procurements/{procurement_id}/score",
        json={"score": 10.0, "fit_score": 0.9, "score_method": "fit"},
        headers={"X-Internal-Token": "wrong"},
    )
    assert bad.status_code == 401


def test_protected_endpoints_require_token(auth_client: TestClient) -> None:
    client = auth_client
    assert client.get("/api/procurements").status_code == 401
    assert client.get("/api/procurements").status_code == 401
    bad = client.get("/api/procurements", headers=_headers("garbage-token"))
    assert bad.status_code == 401


def test_tenderologist_cannot_use_admin_endpoints(auth_client: TestClient) -> None:
    client = auth_client
    token = _login(client, "tender1", "password123")
    headers = _headers(token)

    # Рабочие (тендеролог) эндпоинты доступны.
    assert client.get("/api/procurements", headers=headers).status_code == 200
    assert client.get("/api/config/threshold", headers=headers).status_code == 200

    # Админ-операции — 403.
    assert client.post("/api/parser/start", headers=headers).status_code == 403
    assert client.post("/api/db/clear", headers=headers).status_code == 403
    assert client.put("/api/config", headers=headers, json={}).status_code == 403


def test_register_never_grants_admin_role(auth_client: TestClient) -> None:
    """Регистрация (даже под логином admin) всегда даёт роль tenderologist.

    Роль администратора регистрацией не выдаётся — она задаётся env-сидом
    начального администратора или администратором системы.
    """
    client = auth_client
    resp = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "password123", "password_confirm": "password123"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["role"] == "tenderologist"

    # Тендеролог не имеет доступа к админ-эндпоинту.
    token = _login(client, "admin", "password123")
    assert client.post("/api/db/clear", headers=_headers(token)).status_code == 403


def test_websocket_requires_token(auth_client: TestClient) -> None:
    client = auth_client
    # Без токена соединение отклоняется (code 1008).
    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect("/ws"):
        pass
    assert exc_info.value.code == 1008


def test_websocket_with_token(auth_client: TestClient) -> None:
    client = auth_client
    token = _login(client, "tender1", "password123")
    headers = _headers(token)
    with client.websocket_connect("/ws?token=" + token) as ws:
        r = client.post("/api/db/clear-inactive", headers=headers)
        assert r.status_code == 200
        assert ws.receive_text() == "data-changed"
