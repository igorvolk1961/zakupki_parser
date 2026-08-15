"""Тесты воркера: поведение при недоступности парсера (задача не теряется)."""

from __future__ import annotations

import httpx
import pytest
from fakeredis.aioredis import FakeRedis, FakeServer

from scoring_service.settings import Settings
from scoring_service.worker import ScoringWorker


class _UnreachableParser:
    """Имитация недоступного парсера (парсер ещё не запущен)."""

    async def get_procurement(self, procurement_id: int) -> dict:
        raise httpx.ConnectError("All connection attempts failed", request=httpx.Request("GET", "http://x"))


class _InternalErrorParser:
    """Имитация парсера, отвечающего 500 (перезапуск/сбой)."""

    async def get_procurement(self, procurement_id: int) -> dict:
        resp = httpx.Response(500, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)


class _MissingParser:
    """Имитация парсера без закупки (404 — задача снимается с очереди)."""

    async def get_procurement(self, procurement_id: int) -> dict:
        resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("Not found", request=resp.request, response=resp)


@pytest.fixture
async def worker_queue():
    settings = Settings(score_use_stub=True, parser_retry_backoff_seconds=0.0)
    worker = ScoringWorker(settings)
    worker._queue._client = FakeRedis(server=FakeServer(), decode_responses=True)
    yield worker
    await worker._queue.close()


async def test_transient_parser_error_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _UnreachableParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(133, 27100.0)
    await worker._process_once()
    # Задача должна вернуться в очередь с прежним приоритетом.
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:133")
    assert score == 27100.0
    # И быть снятой с обработки.
    processing = worker._queue._settings.processing_key
    assert await worker._queue._client.zscore(processing, "proc:133") is None


async def test_http_500_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _InternalErrorParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(5, 100.0)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:5")
    assert score == 100.0


async def test_http_404_drops_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _MissingParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(7, 50.0)
    await worker._process_once()
    # 404 — закупка удалена у парсера: задача снимается навсегда.
    assert await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:7") is None
