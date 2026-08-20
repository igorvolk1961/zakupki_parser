"""Тесты broker-очереди с fakeredis."""

from __future__ import annotations

import json

import fakeredis.aioredis as fakeredis_aioredis
import pytest

from scoring_transport.broker.redis_queue import TransportQueue
from scoring_transport.settings import Settings


@pytest.fixture
async def queue():
    server = fakeredis_aioredis.FakeServer()
    queue = TransportQueue(Settings())
    queue._client = fakeredis_aioredis.FakeRedis(server=server, decode_responses=True)
    yield queue
    await queue._client.aclose()


async def test_enqueue_sets_priority(queue) -> None:
    await queue.enqueue(7, 100.0)
    assert queue._client is not None
    score = await queue._client.zscore(queue._settings.jobs_key, "proc:7")
    assert score == 100.0


async def test_enqueue_stage_analysis_routes_to_analysis_queue(queue) -> None:
    await queue.enqueue(9, 50.0, stage="analysis")
    assert queue._client is not None
    assert await queue._client.zscore(queue._settings.analysis_jobs_key, "proc:9") == 50.0
    assert await queue._client.zscore(queue._settings.jobs_key, "proc:9") is None


async def test_results_keys_include_analysis(queue) -> None:
    assert queue._settings.analysis_results_key in queue._results_keys()


async def test_pop_result_roundtrip(queue) -> None:
    assert queue._client is not None
    await queue._client.lpush(queue._settings.results_key, json.dumps({"a": 1}))
    payload = await queue.pop_result(timeout=0.1)
    assert payload == {"a": 1}


async def test_pop_result_from_analysis_queue(queue) -> None:
    assert queue._client is not None
    await queue._client.lpush(
        queue._settings.analysis_results_key,
        json.dumps({"procurement_id": 3, "rag_report": {"tz_found": True}}),
    )
    payload = await queue.pop_result(timeout=0.1)
    assert payload == {"procurement_id": 3, "rag_report": {"tz_found": True}}


async def test_pop_result_empty_timeout(queue) -> None:
    payload = await queue.pop_result(timeout=0.1)
    assert payload is None
