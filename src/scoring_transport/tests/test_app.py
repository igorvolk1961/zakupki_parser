"""Интеграционный тест ingest-эндпоинта транспорта (TestClient + fakeredis)."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis.aioredis as fakeredis_aioredis
from fastapi.testclient import TestClient

import scoring_transport.consumers.results as results_module
import scoring_transport.web.app as app_module
from scoring_transport.settings import Settings


class _FakeParser:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        pass

    async def get_procurement(self, procurement_id: int) -> dict[str, Any]:
        return {"id": procurement_id, "score": 250.0, "score_method": "default", "nmck": 500.0}


class _FakeQueue:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.enqueued: list[tuple[int, float, str]] = []
        self._client = None

    async def connect(self) -> None:
        self._client = fakeredis_aioredis.FakeRedis(decode_responses=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def enqueue(self, procurement_id: int, priority: float, stage: str = "fit") -> None:
        self.enqueued.append((procurement_id, priority, stage))

    async def pop_result(self, timeout: float | None = None) -> dict[str, Any] | None:
        await asyncio.sleep(0.005)
        return None


def _make_app() -> tuple[TestClient, _FakeQueue]:
    settings = Settings(parser_api_url="http://parser", redis_url="redis://fake")
    app_module.ParserApiClient = _FakeParser  # type: ignore[assignment]
    fake_queue = _FakeQueue(settings)
    app_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    results_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    app = app_module.create_app(settings)
    return TestClient(app), fake_queue


def test_ingest_without_priority_uses_priority_default() -> None:
    client, queue = _make_app()
    resp = client.post("/api/scoring/jobs", json={"procurement_id": 42})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "enqueued"
    assert body["priority"] == 0.0
    assert queue.enqueued == [(42, 0.0, "fit")]


def test_ingest_explicit_priority_wins() -> None:
    client, queue = _make_app()
    resp = client.post("/api/scoring/jobs", json={"procurement_id": 42, "priority": 999.0})
    assert resp.status_code == 202
    assert queue.enqueued == [(42, 999.0, "fit")]


def test_ingest_stage_pwin() -> None:
    client, queue = _make_app()
    resp = client.post(
        "/api/scoring/jobs", json={"procurement_id": 42, "priority": 0.7, "stage": "pwin"}
    )
    assert resp.status_code == 202
    assert queue.enqueued == [(42, 0.7, "pwin")]


def test_health() -> None:
    client, _ = _make_app()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ingest_requires_token_when_set() -> None:
    settings = Settings(
        parser_api_url="http://parser", redis_url="redis://fake", auth_token="t0ken"
    )
    app_module.ParserApiClient = _FakeParser  # type: ignore[assignment]
    fake_queue = _FakeQueue(settings)
    app_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    results_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    client = TestClient(app_module.create_app(settings))

    resp = client.post("/api/scoring/jobs", json={"procurement_id": 1})
    assert resp.status_code == 401
    assert (
        client.post(
            "/api/scoring/jobs",
            json={"procurement_id": 1},
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )
