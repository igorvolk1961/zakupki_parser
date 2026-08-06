"""Тесты broker-очереди (fakeredis): приоритет, результаты, восстановление."""

from __future__ import annotations

import json
import time

import fakeredis.aioredis as fakeredis_aioredis
import pytest

from scoring_service.settings import Settings
from scoring_service.transport.redis_queue import ScoringQueue


@pytest.fixture
async def queue():
    server = fakeredis_aioredis.FakeServer()
    q = ScoringQueue(Settings(processing_ttl_seconds=1, processing_recovery_priority=7.0))
    q._client = fakeredis_aioredis.FakeRedis(server=server, decode_responses=True)
    yield q
    await q._client.aclose()


async def test_enqueue_sets_priority(queue) -> None:
    await queue.enqueue(7, 100.0)
    assert queue._client is not None
    score = await queue._client.zscore(queue._settings.jobs_key, "proc:7")
    assert score == 100.0


async def test_pop_job_returns_highest_priority(queue) -> None:
    await queue.enqueue(1, 10.0)
    await queue.enqueue(2, 200.0)
    await queue.enqueue(3, 50.0)
    job = await queue.pop_job()
    assert job == (2, 200.0)


async def test_publish_result_roundtrip(queue) -> None:
    assert queue._client is not None
    await queue.publish_result({"a": 1})
    items = await queue._client.lrange(queue._settings.results_key, 0, -1)
    assert json.loads(items[0]) == {"a": 1}


async def test_recover_stale_requeues_expired(queue) -> None:
    assert queue._client is not None
    # просроченная аренда (score в далёком прошлом)
    await queue._client.zadd(queue._settings.processing_key, {"proc:9": time.time() - 100})
    await queue.recover_stale()
    assert await queue._client.zscore(queue._settings.jobs_key, "proc:9") == 7.0
    assert await queue._client.zscore(queue._settings.processing_key, "proc:9") is None


async def test_recover_stale_keeps_fresh(queue) -> None:
    assert queue._client is not None
    await queue._client.zadd(queue._settings.processing_key, {"proc:5": time.time()})
    await queue.recover_stale()
    assert await queue._client.zscore(queue._settings.jobs_key, "proc:5") is None
    assert await queue._client.zscore(queue._settings.processing_key, "proc:5") is not None


async def test_recover_preserves_claimed_priority(queue) -> None:
    await queue.claim_processing(9, 100.0)
    assert queue._client is not None
    # делаем аренду просроченной
    await queue._client.zadd(queue._settings.processing_key, {"proc:9": time.time() - 100})
    await queue.recover_stale()
    assert await queue._client.zscore(queue._settings.jobs_key, "proc:9") == 100.0
    assert await queue._client.zscore(queue._settings.processing_key, "proc:9") is None
    assert await queue._client.hget(queue._settings.processing_meta_key, "proc:9") is None
