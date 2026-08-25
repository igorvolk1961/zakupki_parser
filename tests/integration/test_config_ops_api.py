"""Интеграционные тесты конфигурации для devops/analyst (требуют PostgreSQL).

Проверяют: GET/PUT config_ops.yaml (секреты не выдаются и не пишутся в YAML),
config_log.yaml, config_parser.yaml (только чтение), raw-YAML и схемы форм.
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

SECRET = "ops-test-secret"


@pytest.fixture(scope="module")
def ops_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
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
    cfgdir = tmp_path_factory.mktemp("configs_ops")
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
    app = create_app(str(cfgdir))
    with TestClient(app) as client:
        yield client
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_AUTH_SECRET", None)
    os.environ.pop("ZAKUPKI_INTERNAL_TOKEN", None)


def _login(client: TestClient) -> str:
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {_login(client)}"}


def test_ops_config_redacts_secrets(ops_client: TestClient) -> None:
    cfg = ops_client.get("/api/config/ops", headers=_auth(ops_client)).json()
    assert "timeout_seconds" in cfg
    assert cfg["db"]["dsn"]
    # Секреты не выдаются.
    assert "secret" not in cfg["auth"]
    assert "internal_token" not in cfg["auth"]
    for block in ("telegram", "max", "webhook"):
        assert "token" not in cfg["notifications"][block]


def test_ops_schema_hides_secrets(ops_client: TestClient) -> None:
    body = ops_client.get("/api/config/ops/schema", headers=_auth(ops_client)).json()
    schema = {f["key"]: f for f in body["schema"]}
    assert {"auth", "db", "notifications", "export_dir"} <= set(schema)
    auth_keys = {f["key"] for f in schema["auth"]["fields"]}
    assert auth_keys == {"enabled", "token_ttl_seconds"}


def test_ops_put_updates_yaml(ops_client: TestClient) -> None:
    headers = _auth(ops_client)
    # Сырой YAML не содержит секретов.
    raw = ops_client.get("/api/config/ops/raw", headers=headers).json()["yaml"]
    assert "auth:" in raw
    assert "secret" not in raw

    cfg = ops_client.get("/api/config/ops", headers=headers).json()
    cfg["export_dir"] = "data/export_test"
    r = ops_client.put("/api/config/ops", headers=headers, json=cfg)
    assert r.status_code == 200, r.text
    assert r.json()["export_dir"] == "data/export_test"

    raw2 = ops_client.get("/api/config/ops/raw", headers=headers).json()["yaml"]
    assert "export_dir: data/export_test" in raw2
    assert "secret" not in raw2

    # Валидация: некорректный тип — 422.
    bad = ops_client.put(
        "/api/config/ops", headers=headers, json={"timeout_seconds": "not-a-number"}
    )
    assert bad.status_code == 422

    # Raw YAML тоже принимается (расширенный режим).
    y = ops_client.get("/api/config/ops/raw", headers=headers).json()["yaml"]
    y = y.replace("data/export_test", "data/export_yaml")
    h = dict(headers)
    h["Content-Type"] = "text/plain"
    r2 = ops_client.put("/api/config/ops", headers=h, content=y)
    assert r2.status_code == 200, r2.text
    assert r2.json()["export_dir"] == "data/export_yaml"


def test_ops_put_auth_enabled_blocked(ops_client: TestClient) -> None:
    """Смена включения авторизации через API запрещена (управляется env)."""
    headers = _auth(ops_client)
    cfg = ops_client.get("/api/config/ops", headers=headers).json()
    current = cfg["auth"]["enabled"]
    cfg["auth"]["enabled"] = not current
    r = ops_client.put("/api/config/ops", headers=headers, json=cfg)
    assert r.status_code == 409
    # Текущее значение не изменилось.
    after = ops_client.get("/api/config/ops", headers=headers).json()
    assert after["auth"]["enabled"] == current


def test_log_config_rejects_absolute_file(ops_client: TestClient) -> None:
    """Путь файла лога должен быть относительным (защита от чтения чужих файлов)."""
    headers = _auth(ops_client)
    cfg = ops_client.get("/api/config/log", headers=headers).json()
    cfg["file"] = "/etc/passwd"
    assert ops_client.put("/api/config/log", headers=headers, json=cfg).status_code == 422
    cfg["file"] = "../data/escape.log"
    assert ops_client.put("/api/config/log", headers=headers, json=cfg).status_code == 422
    cfg["file"] = "data/test.log"
    assert ops_client.put("/api/config/log", headers=headers, json=cfg).status_code == 200


def test_log_config_get_put(ops_client: TestClient) -> None:
    headers = _auth(ops_client)
    cfg = ops_client.get("/api/config/log", headers=headers).json()
    assert "level" in cfg
    cfg["level"] = "DEBUG"
    r = ops_client.put("/api/config/log", headers=headers, json=cfg)
    assert r.status_code == 200, r.text
    assert r.json()["level"] == "DEBUG"
    raw = ops_client.get("/api/config/log/raw", headers=headers).json()["yaml"]
    assert "level: DEBUG" in raw


def test_parser_config_readonly(ops_client: TestClient) -> None:
    headers = _auth(ops_client)
    body = ops_client.get("/api/config/parser", headers=headers).json()
    assert "browser" in body
    assert "retry" in body
    schema = ops_client.get("/api/config/parser/schema", headers=headers).json()["schema"]
    assert {f["key"] for f in schema} >= {"browser", "retry", "request_limits"}
    # Только чтение.
    assert ops_client.put("/api/config/parser", headers=headers, json={}).status_code == 405


def test_service_raw_and_schema(ops_client: TestClient) -> None:
    headers = _auth(ops_client)
    raw = ops_client.get("/api/config/service/raw", headers=headers).json()["yaml"]
    assert "sites:" in raw
    schema = ops_client.get("/api/config/service/schema", headers=headers).json()["schema"]
    sites = next(f for f in schema if f["key"] == "sites")
    # Площадки в форме — из dom-конфигов.
    platform = next(f for f in sites["item"] if f["key"] == "platform_id")
    assert platform["kind"] == "select"
    assert platform["options"]
