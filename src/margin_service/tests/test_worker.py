"""Тесты воркера Margin: расчёт и поведение при недоступности парсера."""

from __future__ import annotations

import json

import httpx
import pytest
from fakeredis.aioredis import FakeRedis, FakeServer

from margin_service.settings import Settings
from margin_service.worker import MarginWorker


class _StubParser:
    """Парсер, возвращающий фиксированную карточку закупки."""

    def __init__(self, record: dict | None = None) -> None:
        self._record = record or {
            "id": 1,
            "subject": "Разработка ПО",
            "nmck": 200.0,
            "fit_score": 0.7,
            "p_win": 0.5,
        }

    async def get_procurement(self, procurement_id: int) -> dict:
        return self._record


class _UnreachableParser:
    async def get_procurement(self, procurement_id: int) -> dict:
        raise httpx.ConnectError(
            "All connection attempts failed", request=httpx.Request("GET", "http://x")
        )


class _InternalErrorParser:
    async def get_procurement(self, procurement_id: int) -> dict:
        resp = httpx.Response(500, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)


class _MissingParser:
    async def get_procurement(self, procurement_id: int) -> dict:
        resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("Not found", request=resp.request, response=resp)


@pytest.fixture
async def worker_queue():
    settings = Settings(parser_retry_backoff_seconds=0.0)
    worker = MarginWorker(settings)
    worker._queue._client = FakeRedis(server=FakeServer(), decode_responses=True)  # noqa: SLF001
    yield worker
    await worker._queue.close()


async def test_margin_publishes_result(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _StubParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(1, 0.35)
    await worker._process_once()

    results = worker._queue._settings.results_key
    payload = await worker._queue._client.lindex(results, 0)
    data = json.loads(payload)
    # margin = 200 × 1.0 = 200; score = 0.7 × 0.5 × 200 = 70.0
    assert data["procurement_id"] == 1
    assert data["score_method"] == "margin"
    assert data["margin"] == 200.0
    assert data["fit_score"] == 0.7
    assert data["p_win"] == 0.5
    assert data["score"] == 70.0


async def test_margin_applies_rate(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _StubParser()
    worker._settings.margin_rate = 0.5
    assert worker._queue._client is not None
    await worker._queue.enqueue(2, 0.35)
    await worker._process_once()

    results = worker._queue._settings.results_key
    payload = await worker._queue._client.lindex(results, 0)
    data = json.loads(payload)
    assert data["margin"] == 100.0
    assert data["score"] == 35.0


async def test_transient_parser_error_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _UnreachableParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(133, 0.5)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:133")
    assert score == 0.5


async def test_http_500_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _InternalErrorParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(5, 0.4)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:5")
    assert score == 0.4


async def test_http_404_drops_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _MissingParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(7, 0.3)
    await worker._process_once()
    assert await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:7") is None
