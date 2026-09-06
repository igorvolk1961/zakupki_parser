"""Интеграционные тесты автозапуска мониторинга парсера при старте сервиса.

Проверяют флаг ``auto_start_monitoring`` в config_ops.yaml (devops):
- true (по умолчанию) — при старте веб-сервиса запускается цикл мониторинга;
- false — мониторинг стартует только вручную с панели devops (POST /api/parser/start),
  статус парсера после старта сервиса — остановлен.
"""

from __future__ import annotations

import asyncio
import os
import re
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

_SECRET = "parser-autostart-secret"


@pytest.fixture(scope="module", autouse=True)
def _prepare_db() -> Iterator[None]:
    """Создаёт схему и администратора (все роли) в тестовой БД."""

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
                "admin",
                await asyncio.to_thread(hash_password, "adminpass"),
                list(ALL_ROLES),
            )
        finally:
            await db.dispose()

    asyncio.run(_setup())
    yield


def _make_app(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    auto_start: bool,
) -> TestClient:
    """Приложение на копии тестовых конфигов с заданным значением флага."""
    cfgdir = tmp_path_factory.mktemp("configs_autostart")
    src = Path(__file__).resolve().parents[2] / "tests" / "configs"
    shutil.copytree(src, cfgdir, dirs_exist_ok=True)
    ops = cfgdir / "config_ops.yaml"
    text = re.sub(
        r"(?m)^auto_start_monitoring:.*$",
        f"auto_start_monitoring: {str(auto_start).lower()}",
        ops.read_text(encoding="utf-8"),
    )
    ops.write_text(text, encoding="utf-8")

    monkeypatch.setenv("ZAKUPKI_DB_DSN", TEST_DSN)
    monkeypatch.setenv("ZAKUPKI_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ZAKUPKI_INTERNAL_TOKEN", "internal-secret")
    # Conftest отключает автозапуск через env; здесь поведением управляет YAML-флаг.
    monkeypatch.delenv("ZAKUPKI_AUTO_START_MONITORING", raising=False)
    monkeypatch.delenv("ZAKUPKI_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ZAKUPKI_ADMIN_PASSWORD", raising=False)
    app = create_app(str(cfgdir))
    return TestClient(app)


def _login(client: TestClient) -> str:
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def test_auto_start_enabled_starts_monitoring_on_boot(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto_start_monitoring=true: цикл мониторинга стартует при подъёме сервиса."""
    spawned: list[object] = []
    monkeypatch.setattr("zakupki_parser.api.app._spawn_parser", lambda state: spawned.append(state))
    client = _make_app(tmp_path_factory, monkeypatch, auto_start=True)
    with client:
        assert spawned, "мониторинг должен автозапуститься при старте сервиса"


def test_auto_start_disabled_waits_manual_start(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto_start_monitoring=false: мониторинг не стартует, статус — остановлен."""
    monkeypatch.setattr(
        "zakupki_parser.api.app._spawn_parser",
        lambda *args: pytest.fail("мониторинг не должен автозапускаться при false"),
    )
    client = _make_app(tmp_path_factory, monkeypatch, auto_start=False)
    with client:
        headers = {"Authorization": f"Bearer {_login(client)}"}
        resp = client.get("/api/parser/status", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["running"] is False
