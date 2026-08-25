"""Интеграционные тесты управления пользователями и ролевого доступа.

Проверяют: CRUD пользователей администратором (создание с ролями
admin/analyst/devops), недоступность роли «user» для выдачи, неприкосновенность
ролей простых пользователей, запрет действий над собой, блокировку/разблокировку
аккаунта (заблокированный не входит), гейтинг эндпоинтов по ролям.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.auth import ALL_ROLES, hash_password
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")

SECRET = "roles-test-secret"


@pytest.fixture(scope="module")
def roles_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Приложение с авторизацией и сервис-аккаунтом (все роли)."""

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
            user = await repo.first_user()
            if user is None:
                user = await repo.create_user(
                    "admin",
                    await asyncio.to_thread(hash_password, "adminpass"),
                    list(ALL_ROLES),
                )
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
    cfgdir = tmp_path_factory.mktemp("configs_roles")
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(
    client: TestClient, admin_token: str, username: str, roles: list[str]
) -> dict[str, Any]:
    resp = client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={"username": username, "password": "password123", "roles": roles},
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _register(client: TestClient, username: str) -> str:
    resp = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "password123",
            "password_confirm": "password123",
        },
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def test_admin_can_create_users_and_list(roles_client: TestClient) -> None:
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")
    users = client.get("/api/users", headers=_auth(admin_token))
    assert users.status_code == 200
    body = users.json()
    assert body["total"] >= 1
    admin_user = next(u for u in body["items"] if u["username"] == "admin")
    assert set(admin_user["roles"]) == set(ALL_ROLES)
    assert admin_user["status"] == "active"
    assert "role" not in admin_user

    created = _create(client, admin_token, "analyst1", ["analyst"])
    assert set(created["roles"]) == {"analyst"}
    # Созданный пользователь может войти.
    token = _login(client, "analyst1", "password123")
    assert client.get("/api/auth/me", headers=_auth(token)).json()["username"] == "analyst1"


def test_create_user_rejects_user_role_and_empty(roles_client: TestClient) -> None:
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")
    bad = client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={"username": "x1", "password": "password123", "roles": ["user"]},
    )
    assert bad.status_code == 422
    empty = client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={"username": "x2", "password": "password123", "roles": []},
    )
    assert empty.status_code == 422
    dup = client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={"username": "analyst1", "password": "password123", "roles": ["devops"]},
    )
    assert dup.status_code == 409


def test_simple_user_roles_untouchable(roles_client: TestClient) -> None:
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")
    _register(client, "plain1")
    users = client.get("/api/users", headers=_auth(admin_token)).json()
    plain = next(u for u in users["items"] if u["username"] == "plain1")
    assert plain["roles"] == ["user"]

    r = client.patch(
        f"/api/users/{plain['id']}/roles",
        headers=_auth(admin_token),
        json={"roles": ["analyst"]},
    )
    assert r.status_code == 409
    # Простой пользователь не может изменить и собственные роли (нет админ-доступа).
    plain_token = _login(client, "plain1", "password123")
    assert (
        client.patch(
            f"/api/users/{plain['id']}/roles",
            headers=_auth(plain_token),
            json={"roles": ["analyst"]},
        ).status_code
        == 403
    )


def test_cannot_touch_self(roles_client: TestClient) -> None:
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")
    users = client.get("/api/users", headers=_auth(admin_token)).json()
    me = next(u for u in users["items"] if u["username"] == "admin")

    assert (
        client.patch(
            f"/api/users/{me['id']}/roles",
            headers=_auth(admin_token),
            json={"roles": ["analyst"]},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/users/{me['id']}/status",
            headers=_auth(admin_token),
            json={"status": "blocked"},
        ).status_code
        == 403
    )
    assert client.delete(f"/api/users/{me['id']}", headers=_auth(admin_token)).status_code == 403


def test_blocked_user_cannot_login(roles_client: TestClient) -> None:
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")
    _create(client, admin_token, "blockme", ["devops"])
    users = client.get("/api/users", headers=_auth(admin_token)).json()
    target = next(u for u in users["items"] if u["username"] == "blockme")

    token = _login(client, "blockme", "password123")
    # Блокируем — существующий токен перестаёт работать, вход закрыт.
    r = client.patch(
        f"/api/users/{target['id']}/status",
        headers=_auth(admin_token),
        json={"status": "blocked"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "blocked"
    assert client.get("/api/procurements", headers=_auth(token)).status_code == 403
    assert (
        client.post(
            "/api/auth/login", json={"username": "blockme", "password": "password123"}
        ).status_code
        == 403
    )

    # Разблокировка — вход снова работает.
    client.patch(
        f"/api/users/{target['id']}/status",
        headers=_auth(admin_token),
        json={"status": "active"},
    )
    assert _login(client, "blockme", "password123")


def test_user_role_preserved_on_role_edit(roles_client: TestClient) -> None:
    """Роль «user» не снимается при смене остальных ролей пользователя."""
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")

    async def _seed_mixed() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            user = await repo.create_user("mixed", "hash", ["user", "analyst"])
            return user.id
        finally:
            await db.dispose()

    user_id = asyncio.run(_seed_mixed())
    r = client.patch(
        f"/api/users/{user_id}/roles",
        headers=_auth(admin_token),
        json={"roles": ["analyst", "devops"]},
    )
    assert r.status_code == 200, r.text
    assert set(r.json()["roles"]) == {"user", "analyst", "devops"}


def test_delete_user(roles_client: TestClient) -> None:
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")
    created = _create(client, admin_token, "gone", ["analyst"])
    assert (
        client.delete(f"/api/users/{created['id']}", headers=_auth(admin_token)).status_code == 204
    )
    # Пользователь больше не может войти.
    assert (
        client.post(
            "/api/auth/login", json={"username": "gone", "password": "password123"}
        ).status_code
        == 401
    )
    # Повторное удаление — 404.
    r404 = client.delete(f"/api/users/{created['id']}", headers=_auth(admin_token))
    assert r404.status_code == 404


def test_role_gating(roles_client: TestClient) -> None:
    client = roles_client
    admin_token = _login(client, "admin", "adminpass")
    _create(client, admin_token, "analyst2", ["analyst"])
    _create(client, admin_token, "ops2", ["devops"])
    _register(client, "simple2")

    analyst_token = _login(client, "analyst2", "password123")
    ops_token = _login(client, "ops2", "password123")
    simple_token = _login(client, "simple2", "password123")

    # Простой пользователь: базовые вкладки доступны, ролевые — нет.
    assert client.get("/api/procurements", headers=_auth(simple_token)).status_code == 200
    assert client.get("/api/config/threshold", headers=_auth(simple_token)).status_code == 200
    assert client.get("/api/users", headers=_auth(simple_token)).status_code == 403
    assert client.get("/api/config", headers=_auth(simple_token)).status_code == 403
    assert client.get("/api/config/ops", headers=_auth(simple_token)).status_code == 403
    assert client.get("/api/logs/tail", headers=_auth(simple_token)).status_code == 403
    assert client.get("/api/reference", headers=_auth(simple_token)).status_code == 403

    # Аналитик: конфиг сервиса, промпты и справочники; не админ/devops.
    assert client.get("/api/config", headers=_auth(analyst_token)).status_code == 200
    assert client.get("/api/config/service/schema", headers=_auth(analyst_token)).status_code == 200
    assert client.get("/api/prompts", headers=_auth(analyst_token)).status_code == 200
    assert client.get("/api/reference", headers=_auth(analyst_token)).status_code == 200
    assert client.get("/api/users", headers=_auth(analyst_token)).status_code == 403
    assert client.get("/api/config/ops", headers=_auth(analyst_token)).status_code == 403
    assert client.get("/api/logs/tail", headers=_auth(analyst_token)).status_code == 403

    # DevOps: конфигурация, логи, парсер; не админ/аналитик.
    assert client.get("/api/config/ops", headers=_auth(ops_token)).status_code == 200
    assert client.get("/api/config/log", headers=_auth(ops_token)).status_code == 200
    assert client.get("/api/logs/tail", headers=_auth(ops_token)).status_code == 200
    assert client.get("/api/config/parser", headers=_auth(ops_token)).status_code == 200
    assert client.get("/api/users", headers=_auth(ops_token)).status_code == 403
    assert client.get("/api/config", headers=_auth(ops_token)).status_code == 403
    assert client.get("/api/reference", headers=_auth(ops_token)).status_code == 403

    # Админ: управление пользователями и панель парсера.
    assert client.get("/api/users", headers=_auth(admin_token)).status_code == 200
    assert client.post("/api/parser/start", headers=_auth(admin_token)).status_code in (200, 409)
