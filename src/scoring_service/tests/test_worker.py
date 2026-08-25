"""Тесты воркера: поведение при недоступности парсера (задача не теряется)."""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest
from fakeredis.aioredis import FakeRedis, FakeServer

from scoring_service.settings import Settings
from scoring_service.worker import ScoringWorker


class _UnreachableParser:
    """Имитация недоступного парсера (парсер ещё не запущен)."""

    async def get_procurement(self, procurement_id: int) -> dict:
        raise httpx.ConnectError(
            "All connection attempts failed", request=httpx.Request("GET", "http://x")
        )


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


class _OkParser:
    """Имитация парсера, отдающего закупку (дальше скоринг)."""

    async def get_procurement(self, procurement_id: int) -> dict:
        return {"id": procurement_id, "score": 0.5}

    async def get_active_client(self, internal_token: str | None = None) -> dict:
        return {"competencies": "test competencies"}


class _TimeoutScorer:
    """Имитация LLM-таймаута (openai.APITimeoutError)."""

    def score(
        self,
        record: dict[str, Any],
        competencies: str,
        procurement_id: int | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_name: str = "scoring_job",
    ) -> object:
        raise openai.APITimeoutError(request=httpx.Request("POST", "http://llm/chat/completions"))


class _RejectedScorer:
    """Имитация постоянной ошибки LLM (4xx — неверный запрос/ключ)."""

    def score(
        self,
        record: dict[str, Any],
        competencies: str,
        procurement_id: int | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_name: str = "scoring_job",
    ) -> object:
        raise openai.BadRequestError(
            "invalid request",
            response=httpx.Response(400, request=httpx.Request("POST", "http://llm")),
            body=None,
        )


@pytest.fixture
async def worker_queue():
    settings = Settings(
        score_use_stub=True,
        parser_retry_backoff_seconds=0.0,
        llm_retry_backoff_seconds=0.0,
    )
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


async def test_llm_timeout_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    worker._scorer = _TimeoutScorer()
    assert worker._queue._client is not None
    await worker._queue.enqueue(306, 1781072448.0)
    await worker._process_once()
    # Задача должна вернуться в очередь с прежним приоритетом (не потеряться).
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:306")
    assert score == 1781072448.0
    # И счётчик ретраев инкрементирован.
    retries = await worker._queue._client.hget(worker._queue._settings.jobs_retry_key, "proc:306")
    assert retries == "1"
    # И быть снятой с обработки.
    processing = worker._queue._settings.processing_key
    assert await worker._queue._client.zscore(processing, "proc:306") is None


async def test_llm_timeout_dropped_after_max_attempts(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    worker._scorer = _TimeoutScorer()
    assert worker._queue._client is not None
    # До обработки уже было llm_retry_max_attempts неудач.
    for _ in range(worker._settings.llm_retry_max_attempts):
        await worker._queue.increment_retries(306)
    await worker._queue.enqueue(306, 1.0)
    await worker._process_once()
    # Лимит исчерпан: задача снимается навсегда, счётчик обнулён.
    assert await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:306") is None
    retries = await worker._queue._client.hget(worker._queue._settings.jobs_retry_key, "proc:306")
    assert retries is None


async def test_llm_rejection_drops_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    worker._scorer = _RejectedScorer()
    assert worker._queue._client is not None
    await worker._queue.enqueue(400, 1.0)
    await worker._process_once()
    # 4xx — постоянная ошибка: задача снимается навсегда без ретраев.
    assert await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:400") is None
    retries = await worker._queue._client.hget(worker._queue._settings.jobs_retry_key, "proc:400")
    assert retries is None


async def test_success_resets_retries(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    # score_use_stub=True: скоринг вернёт score из карточки без LLM.
    assert worker._queue._client is not None
    # Были сбои LLM, потом успех — счётчик обнуляется.
    for _ in range(2):
        await worker._queue.increment_retries(9)
    await worker._queue.enqueue(9, 1.0)
    await worker._process_once()
    retries = await worker._queue._client.hget(worker._queue._settings.jobs_retry_key, "proc:9")
    assert retries is None
    # И результат опубликован.
    results = worker._queue._settings.results_key
    assert await worker._queue._client.llen(results) == 1
