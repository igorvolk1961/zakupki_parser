"""Тесты воркера P(win): расчёт и поведение при недоступности парсера."""

from __future__ import annotations

import json

import httpx
import pytest
from fakeredis.aioredis import FakeRedis, FakeServer

from pwin_service.settings import Settings
from pwin_service.worker import PwinWorker


class _StubParser:
    """Парсер, возвращающий фиксированную карточку закупки."""

    def __init__(self, record: dict | None = None) -> None:
        self._record = record or {
            "id": 1,
            "subject": "Разработка ИИ-агента",
            "nmck": 1_000_000,
            "fit_score": 0.7,
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
    # config.yaml включает use_stub — для теста формулы включаем расчёт по карточке.
    settings = Settings(parser_retry_backoff_seconds=0.0, use_stub=False)
    worker = PwinWorker(settings)
    worker._queue._client = FakeRedis(server=FakeServer(), decode_responses=True)  # noqa: SLF001
    yield worker
    await worker._queue.close()


async def test_pwin_publishes_result(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _StubParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(1, 0.7, profile_id=1)
    await worker._process_once()

    results = worker._queue._settings.results_key
    payload = await worker._queue._client.lindex(results, 0)
    data = json.loads(payload)
    # ИИ-закупка: base 0.4 × k_ai 1.8 = 0.72; score = fit 0.7 × 0.72 = 0.504
    assert data["procurement_id"] == 1
    assert data["score_method"] == "pwin"
    assert data["p_win"] == 0.72
    assert data["fit_score"] == 0.7
    assert data["score"] == 0.504


async def test_pwin_stub_returns_constant() -> None:
    """Заглушка: P(win) = константа stub_pwin, без расчёта по карточке."""
    settings = Settings(parser_retry_backoff_seconds=0.0, use_stub=True, stub_pwin=0.42)
    worker = PwinWorker(settings)
    worker._queue._client = FakeRedis(server=FakeServer(), decode_responses=True)  # noqa: SLF001
    worker._parser = _StubParser()
    try:
        await worker._queue.enqueue(1, 0.7, profile_id=1)
        await worker._process_once()
        payload = await worker._queue._client.lindex(settings.results_key, 0)
        data = json.loads(payload)
        assert data["p_win"] == 0.42
        assert data["score"] == round(0.7 * 0.42, 4)
    finally:
        await worker._queue.close()


async def test_transient_parser_error_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _UnreachableParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(133, 0.7, profile_id=1)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:133:pf:1")
    assert score == 0.7


async def test_http_500_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _InternalErrorParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(5, 0.5, profile_id=1)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:5:pf:1")
    assert score == 0.5


async def test_http_404_drops_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _MissingParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(7, 0.4, profile_id=1)
    await worker._process_once()
    assert (
        await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:7:pf:1") is None
    )
