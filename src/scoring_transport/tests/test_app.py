"""Интеграционный тест ingest-эндпоинта транспорта (TestClient + fakeredis)."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis.aioredis as fakeredis_aioredis
import httpx
from fastapi.testclient import TestClient

import scoring_transport.consumers.results as results_module
import scoring_transport.web.app as app_module
from scoring_transport.settings import Settings


class _FakeParser:
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        internal_token: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.internal_token = internal_token

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

    async def enqueue(
        self,
        procurement_id: int,
        priority: float,
        stage: str = "fit",
        profile_id: int | None = None,
    ) -> None:
        self.enqueued.append((procurement_id, priority, stage, profile_id))

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
    resp = client.post("/api/scoring/jobs", json={"procurement_id": 42, "profile_id": 1})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "enqueued"
    assert body["priority"] == 0.0
    assert queue.enqueued == [(42, 0.0, "fit", 1)]


def test_ingest_explicit_priority_wins() -> None:
    client, queue = _make_app()
    resp = client.post(
        "/api/scoring/jobs", json={"procurement_id": 42, "priority": 999.0, "profile_id": 1}
    )
    assert resp.status_code == 202
    assert queue.enqueued == [(42, 999.0, "fit", 1)]


def test_ingest_stage_pwin() -> None:
    client, queue = _make_app()
    resp = client.post(
        "/api/scoring/jobs",
        json={"procurement_id": 42, "priority": 0.7, "stage": "pwin", "profile_id": 1},
    )
    assert resp.status_code == 202
    assert queue.enqueued == [(42, 0.7, "pwin", 1)]


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


def test_ingest_passes_internal_token_to_parser() -> None:
    """Внутренний токен парсера уходит в ParserApiClient (X-Internal-Token)."""
    calls: list[tuple[str, str | None]] = []

    class _SpyParser(_FakeParser):
        def __init__(
            self,
            base_url: str,
            timeout: float = 30.0,
            internal_token: str | None = None,
        ) -> None:
            calls.append((base_url, internal_token))
            super().__init__(base_url, timeout, internal_token)

    settings = Settings(
        parser_api_url="http://parser",
        redis_url="redis://fake",
        parser_internal_token="sekret",
    )
    app_module.ParserApiClient = _SpyParser  # type: ignore[assignment]
    fake_queue = _FakeQueue(settings)
    app_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    results_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    client = TestClient(app_module.create_app(settings))

    resp = client.post("/api/scoring/jobs", json={"procurement_id": 42, "profile_id": 1})
    assert resp.status_code == 202
    assert ("http://parser", "sekret") in calls


class _AuthErrorParser:
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        internal_token: str | None = None,
    ) -> None:
        pass

    async def get_procurement(self, procurement_id: int) -> dict[str, Any]:
        req = httpx.Request("GET", f"http://parser/api/procurements/{procurement_id}")
        raise httpx.HTTPStatusError(
            "Unauthorized", request=req, response=httpx.Response(401, request=req)
        )


def test_ingest_502_includes_upstream_status() -> None:
    """Сбой апстрима (например, 401 от парсера) отдаётся как 502 с реальным кодом."""
    settings = Settings(parser_api_url="http://parser", redis_url="redis://fake")
    app_module.ParserApiClient = _AuthErrorParser  # type: ignore[assignment]
    fake_queue = _FakeQueue(settings)
    app_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    results_module.TransportQueue = lambda s: fake_queue  # type: ignore[assignment]
    client = TestClient(app_module.create_app(settings))

    resp = client.post("/api/scoring/jobs", json={"procurement_id": 1, "profile_id": 1})
    assert resp.status_code == 502
    assert "HTTP 401" in resp.json()["detail"]
