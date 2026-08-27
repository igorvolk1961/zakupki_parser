"""Интеграционные тесты просмотра логов (требуют PostgreSQL).

Проверяют: хвост файла лога, фильтр по уровню (ошибки/предупреждения),
текстовый поиск, диапазон дат и отсутствие файла.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.auth import ALL_ROLES, hash_password
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

SECRET = "logs-test-secret"

LOG_LINES = [
    "2026-08-25 10:00:00,123 INFO  [app] старт сервиса",
    "2026-08-25 10:01:00,456 ERROR [parser] ошибка сети на zakupki_mos",
    "2026-08-25 10:02:00,789 WARNING [app] задержка ответа площадки",
    "2026-08-25 11:00:00,000 INFO  [app] проход завершён",
    "2026-08-25 11:01:00,000 CRITICAL [parser] критическая ошибка",
]


@pytest.fixture(scope="module")
def logs_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
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
                    "competencies": COMP_JSON,
                    "keywords": [],
                    "exclusion_words": [],
                    "questions": [],
                },
                user.id,
            )
        finally:
            await db.dispose()

    asyncio.run(_setup())
    cfgdir = tmp_path_factory.mktemp("configs_logs")
    src = Path(__file__).resolve().parents[2] / "tests" / "configs"
    shutil.copytree(src, cfgdir, dirs_exist_ok=True)
    logfile = cfgdir.parent / "test_parser.log"
    logfile.write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")
    log = cfgdir / "config_log.yaml"
    log.write_text(
        log.read_text(encoding="utf-8").replace("file: null", f"file: {logfile}"),
        encoding="utf-8",
    )
    ops = cfgdir / "config_ops.yaml"
    ops.write_text(
        ops.read_text(encoding="utf-8") + "\nauth:\n  token_ttl_seconds: 3600\n",
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


def _headers(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_logs_tail_default(logs_client: TestClient) -> None:
    data = logs_client.get("/api/logs/tail", headers=_headers(logs_client)).json()
    assert data["file_exists"] is True
    assert data["count"] == len(LOG_LINES)
    assert data["truncated"] is False
    assert data["lines"] == LOG_LINES


def test_logs_tail_level_filter(logs_client: TestClient) -> None:
    h = _headers(logs_client)
    err = logs_client.get("/api/logs/tail", params={"level": "error"}, headers=h).json()
    assert len(err["lines"]) == 2  # ERROR + CRITICAL
    assert all("ERROR" in line or "CRITICAL" in line for line in err["lines"])

    warn = logs_client.get("/api/logs/tail", params={"level": "warning"}, headers=h).json()
    assert len(warn["lines"]) == 1
    assert "WARNING" in warn["lines"][0]


def test_logs_tail_search(logs_client: TestClient) -> None:
    h = _headers(logs_client)
    data = logs_client.get("/api/logs/tail", params={"q": "ошибка"}, headers=h).json()
    assert len(data["lines"]) == 2
    assert all("ошибка" in line for line in data["lines"])


def test_logs_tail_date_range(logs_client: TestClient) -> None:
    h = _headers(logs_client)
    data = logs_client.get(
        "/api/logs/tail",
        params={"from": "2026-08-25T10:02:00", "to": "2026-08-25T10:59:59"},
        headers=h,
    ).json()
    assert len(data["lines"]) == 1
    assert "WARNING" in data["lines"][0]


def test_logs_tail_aware_datetime_no_crash(logs_client: TestClient) -> None:
    """ISO-время с часовым поясом (браузерный toISOString) не роняет эндпоинт."""
    h = _headers(logs_client)
    data = logs_client.get(
        "/api/logs/tail",
        params={"from": "2026-08-25T00:00:00Z", "to": "2026-08-25T23:59:59Z"},
        headers=h,
    ).json()
    assert data["count"] == len(LOG_LINES)


def test_logs_tail_missing_file(logs_client: TestClient, tmp_path: Path) -> None:
    state = cast(Any, logs_client.app).state.parser
    original = state.cfg.logging.file
    state.cfg.logging.file = str(tmp_path / "nope.log")
    try:
        data = logs_client.get("/api/logs/tail", headers=_headers(logs_client)).json()
        assert data["file_exists"] is False
        assert data["lines"] == []
    finally:
        state.cfg.logging.file = original
