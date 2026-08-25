"""Интеграционные тесты страницы справочников (требуют PostgreSQL).

Проверяют: список справочных таблиц (реестр), CRUD строк license_types и
experience_confirmation_types, конфликты уникальности (409), защиту эндпоинтов
ролью analyst (401/403).
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
from zakupki_parser.auth import ALL_ROLES, hash_password
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")

SECRET = "reference-test-secret"


@pytest.fixture(scope="module")
def ref_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Приложение с выключенной авторизацией (dev-режим): эндпоинты открыты."""

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
                user = await repo.create_user("admin", "test-hash", list(ALL_ROLES))
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
            await repo.ensure_reference_data()
        finally:
            await db.dispose()

    asyncio.run(_setup())
    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    # dev-режим: выключаем авторизацию явно (репозиторий .env может включать её).
    os.environ["ZAKUPKI_AUTH_ENABLED"] = "false"
    app = create_app()
    with TestClient(app) as client:
        yield client
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_AUTH_ENABLED", None)


@pytest.fixture(scope="module")
def ref_auth_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Приложение с включённой авторизацией (для проверки роли администратора)."""

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
            await repo.ensure_reference_data()
        finally:
            await db.dispose()

    asyncio.run(_setup())
    cfgdir = tmp_path_factory.mktemp("configs_ref")
    src = Path(__file__).resolve().parents[2] / "tests" / "configs"
    shutil.copytree(src, cfgdir, dirs_exist_ok=True)
    ops = cfgdir / "config_ops.yaml"
    ops.write_text(
        ops.read_text(encoding="utf-8") + "\nauth:\n  enabled: true\n  token_ttl_seconds: 3600\n",
        encoding="utf-8",
    )
    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    os.environ["ZAKUPKI_AUTH_ENABLED"] = "true"
    os.environ["ZAKUPKI_AUTH_SECRET"] = SECRET
    os.environ.pop("ZAKUPKI_ADMIN_USERNAME", None)
    os.environ.pop("ZAKUPKI_ADMIN_PASSWORD", None)
    app = create_app(str(cfgdir))
    with TestClient(app) as client:
        yield client
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_AUTH_ENABLED", None)
    os.environ.pop("ZAKUPKI_AUTH_SECRET", None)


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_reference_tables_listed(ref_client: TestClient) -> None:
    client = ref_client
    r = client.get("/api/reference")
    assert r.status_code == 200
    keys = {t["key"] for t in r.json()}
    assert {"license_types", "experience_confirmation_types"} <= keys
    lic = next(t for t in r.json() if t["key"] == "license_types")
    assert {c["key"] for c in lic["columns"]} == {"code", "name", "sort_order"}
    assert lic["title"]


def test_reference_rows_seeded(ref_client: TestClient) -> None:
    client = ref_client
    r = client.get("/api/reference/license_types")
    assert r.status_code == 200
    body = r.json()
    codes = {row["code"] for row in body["items"]}
    assert {"fstek", "fsb", "mincifry", "other"} <= codes
    # Строка содержит id и колонки редактора.
    row = body["items"][0]
    assert set(row) >= {"id", "code", "name", "sort_order"}

    conf = client.get("/api/reference/experience_confirmation_types")
    assert conf.status_code == 200
    assert {row["code"] for row in conf.json()["items"]} == {"platform", "documents", "registry"}


def test_reference_unknown_table_404(ref_client: TestClient) -> None:
    r = ref_client.get("/api/reference/no_such_table")
    assert r.status_code == 404


def test_license_type_crud(ref_client: TestClient) -> None:
    client = ref_client
    created = client.post(
        "/api/reference/license_types",
        json={"code": "test_lic", "name": "Тестовая лицензия", "sort_order": 99},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["code"] == "test_lic"
    assert body["sort_order"] == 99
    lid = body["id"]

    # Дубликат кода — 409.
    dup = client.post(
        "/api/reference/license_types",
        json={"code": "test_lic", "name": "Дубликат"},
    )
    assert dup.status_code == 409

    # Валидация полей — 422.
    bad = client.post("/api/reference/license_types", json={"code": "", "name": "X"})
    assert bad.status_code == 422

    updated = client.put(
        f"/api/reference/license_types/{lid}",
        json={"code": "test_lic", "name": "Тестовая лицензия v2", "sort_order": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Тестовая лицензия v2"

    assert (
        client.put(
            "/api/reference/license_types/999999", json={"code": "x", "name": "y"}
        ).status_code
        == 404
    )

    assert client.delete(f"/api/reference/license_types/{lid}").status_code == 204
    assert client.delete(f"/api/reference/license_types/{lid}").status_code == 404


def test_confirmation_type_crud(ref_client: TestClient) -> None:
    client = ref_client
    created = client.post(
        "/api/reference/experience_confirmation_types",
        json={"code": "test_confirm", "name": "Тестовое подтверждение", "sort_order": 5},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    dup = client.post(
        "/api/reference/experience_confirmation_types",
        json={"code": "test_confirm", "name": "Дубликат"},
    )
    assert dup.status_code == 409

    updated = client.put(
        f"/api/reference/experience_confirmation_types/{cid}",
        json={"code": "test_confirm", "name": "Тестовое подтверждение v2"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Тестовое подтверждение v2"

    assert client.delete(f"/api/reference/experience_confirmation_types/{cid}").status_code == 204


def test_delete_used_license_type_conflict(ref_client: TestClient) -> None:
    """Тип лицензии, на который ссылается профиль, удалить нельзя (FK RESTRICT)."""
    client = ref_client
    r = client.post(
        "/api/clients", json={"name": "ref-conflict", "competencies": "C", "keywords": []}
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    lic = client.post(
        "/api/reference/license_types",
        json={"code": "used_lic", "name": "Используемый тип"},
    )
    assert lic.status_code == 201
    lid = lic.json()["id"]

    assert (
        client.post(
            f"/api/clients/{pid}/licenses",
            json={"license_type_id": lid, "number": "Л-1"},
        ).status_code
        == 200
    )

    r = client.delete(f"/api/reference/license_types/{lid}")
    assert r.status_code == 409

    assert client.delete(f"/api/clients/{pid}").status_code == 204
    assert client.delete(f"/api/reference/license_types/{lid}").status_code == 204


def test_seed_code_rename_blocked(ref_client: TestClient) -> None:
    """Код предустановленной записи (сид BR-03) переименовать нельзя (409)."""
    client = ref_client
    fstek = next(
        row
        for row in client.get("/api/reference/license_types").json()["items"]
        if row["code"] == "fstek"
    )
    rename = client.put(
        f"/api/reference/license_types/{fstek['id']}",
        json={"code": "renamed", "name": "Другое имя", "sort_order": 1},
    )
    assert rename.status_code == 409

    # Имя/порядок предустановленной записи менять можно.
    ok = client.put(
        f"/api/reference/license_types/{fstek['id']}",
        json={"code": "fstek", "name": "ФСТЭК (обновлено)", "sort_order": 1},
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "ФСТЭК (обновлено)"

    platform = next(
        row
        for row in client.get("/api/reference/experience_confirmation_types").json()["items"]
        if row["code"] == "platform"
    )
    assert (
        client.put(
            f"/api/reference/experience_confirmation_types/{platform['id']}",
            json={"code": "portal", "name": "X"},
        ).status_code
        == 409
    )


def test_reference_analyst_only(ref_auth_client: TestClient) -> None:
    client = ref_auth_client
    # Аноним — 401.
    assert client.get("/api/reference").status_code == 401
    # Простой пользователь — 403.
    token = _login(client, "admin", "adminpass")
    reg = client.post(
        "/api/auth/register",
        json={
            "username": "ref_tender",
            "password": "password123",
            "password_confirm": "password123",
        },
    )
    assert reg.status_code == 200
    tender_token = str(reg.json()["access_token"])
    assert client.get("/api/reference", headers=_auth(tender_token)).status_code == 403
    # Пользователь с ролью analyst (у админа все роли) — 200.
    assert client.get("/api/reference", headers=_auth(token)).status_code == 200
