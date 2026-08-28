"""Интеграционные тесты конфигурации для devops/analyst (требуют PostgreSQL).

Проверяют: GET/PUT config_ops.yaml (секреты не выдаются и не пишутся в YAML),
config_log.yaml, config_parser.yaml (только чтение), raw-YAML и схемы форм.
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

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")

SECRET = "ops-test-secret"
# Компетенции — всегда канонический JSON схемы Profile (BR-07): raw-строка не
# проходит валидацию при сохранении профиля в сиде.
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
    cfgdir = tmp_path_factory.mktemp("configs_ops")
    src = Path(__file__).resolve().parents[2] / "tests" / "configs"
    shutil.copytree(src, cfgdir, dirs_exist_ok=True)
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
    assert auth_keys == {"token_ttl_seconds"}


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


def test_parser_config_editable(ops_client: TestClient) -> None:
    """Вкладка «Парсер» редактируемая (BR: форма + расширенный режим)."""
    headers = _auth(ops_client)
    body = ops_client.get("/api/config/parser", headers=headers).json()
    assert "browser" in body
    assert "retry" in body
    schema = ops_client.get("/api/config/parser/schema", headers=headers).json()["schema"]
    assert {f["key"] for f in schema} >= {"browser", "retry", "request_limits"}
    # Правка в разумных пределах: GET->PUT round-trip сохраняет конфиг.
    r = ops_client.put("/api/config/parser", headers=headers, json=body)
    assert r.status_code == 200
    assert r.json()["retry"] == body["retry"]


def test_service_raw_and_schema(ops_client: TestClient) -> None:
    headers = _auth(ops_client)
    raw = ops_client.get("/api/config/service/raw", headers=headers).json()["yaml"]
    assert "sites:" in raw
    schema = ops_client.get("/api/config/service/schema", headers=headers).json()["schema"]
    sites = next(f for f in schema if f["key"] == "sites")
    # Площадки в форме — из справочника platforms: platform_id вводится ключом,
    # название/URL подтягиваются (derived), поэтому в форме это plain-строка.
    platform = next(f for f in sites["item"] if f["key"] == "platform_id")
    assert platform["kind"] == "str"
    assert platform["plain"] is True


# --- Вкладка «Сервисы»: конфиг + .env фоновых сервисов (devops) -----------
def _service_root(ops_client: TestClient) -> Path:
    """Корень проекта, где лежат src/<service>/config.yaml и .env."""
    app = cast(Any, ops_client).app
    configs_dir = Path(app.state.parser.configs_dir)
    return configs_dir.parent


def test_services_config_api_roundtrip(ops_client: TestClient) -> None:
    """GET/PUT config.yaml и .env сервиса: секреты не выдаются, .env редактируется."""
    svc_dir = _service_root(ops_client) / "src" / "scoring_service"
    svc_dir.mkdir(parents=True, exist_ok=True)
    (svc_dir / "config.yaml").write_text(
        "llm_base_url: http://localhost:8000/v1\nllm_model: gpt-4o-mini\n", encoding="utf-8"
    )
    (svc_dir / ".env").write_text("SCORE_LLM_API_KEY=sk-test\n", encoding="utf-8")

    headers = _auth(ops_client)

    # GET config: секреты вырезаны.
    conf = ops_client.get("/api/services/scoring/config", headers=headers).json()
    assert conf["llm_model"] == "gpt-4o-mini"
    assert "llm_api_key" not in conf
    assert "auth_token" not in conf

    # Схема формы — без секретов.
    schema = ops_client.get("/api/services/scoring/schema", headers=headers).json()["schema"]
    keys = {f["key"] for f in schema}
    assert {"llm_base_url", "llm_model", "embedding_filter_threshold"} <= keys
    assert not (keys & {"llm_api_key", "auth_token", "giga_client_secret"})

    # Raw YAML — без секретов.
    raw = ops_client.get("/api/services/scoring/raw", headers=headers).json()["yaml"]
    assert "llm_api_key" not in raw

    # .env читается и сохраняется.
    env = ops_client.get("/api/services/scoring/env", headers=headers).json()
    assert env["exists"] is True
    assert "SCORE_LLM_API_KEY" in env["content"]

    # PUT формы (config.yaml без секретов).
    conf["llm_model"] = "deepseek-v4-flash"
    r = ops_client.put("/api/services/scoring/config", headers=headers, json=conf)
    assert r.status_code == 200, r.text
    assert r.json()["llm_model"] == "deepseek-v4-flash"

    # PUT .env (raw text) — секреты живут в .env.
    env_headers = dict(headers)
    env_headers["Content-Type"] = "text/plain"
    r = ops_client.put(
        "/api/services/scoring/env", headers=env_headers, content="SCORE_LLM_API_KEY=sk-ok\n"
    )
    assert r.status_code == 200, r.text

    # Некорректный .env — 422.
    r = ops_client.put("/api/services/scoring/env", headers=env_headers, content="BAD LINE\n")
    assert r.status_code == 422

    # Недопустимый сервис — 404.
    assert ops_client.get("/api/services/nope/config", headers=headers).status_code == 404


def test_services_analysis_config_and_schema(ops_client: TestClient) -> None:
    """Вкладка services/analysis: модель и секреты."""
    svc_dir = _service_root(ops_client) / "src" / "analysis_service"
    svc_dir.mkdir(parents=True, exist_ok=True)
    (svc_dir / "config.yaml").write_text(
        "llm_model: deepseek-chat\nchunk_max_chars: 1500\n", encoding="utf-8"
    )
    headers = _auth(ops_client)
    conf = ops_client.get("/api/services/analysis/config", headers=headers).json()
    assert conf["llm_model"] == "deepseek-chat"
    schema = ops_client.get("/api/services/analysis/schema", headers=headers).json()["schema"]
    keys = {f["key"] for f in schema}
    assert {"llm_base_url", "embedding_base_url", "chunk_max_chars", "top_k"} <= keys
    assert not (keys & {"llm_api_key", "embedding_api_key", "parser_internal_token"})
