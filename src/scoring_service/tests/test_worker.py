"""Тесты воркера: поведение при недоступности парсера (задача не теряется)."""

from __future__ import annotations

from types import SimpleNamespace
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


# Структурированный профиль активного клиента (схема Profile, BR-07) — то, что
# отдаёт парсер в ``/api/clients/active``. Свободный текст/legacy-markdown здесь не
# подходит: ``profile_to_texts`` принимает только структурированное значение.
_PROFILE: dict[str, Any] = {
    "name": "Тестовый Поставщик",
    "positioning": "Разработка и внедрение ИИ-решений",
    "breadth": "broad",
    "competencies": [
        {
            "area": "Разработка ИИ",
            "description": "проектирование и внедрение моделей",
            "examples": [],
        }
    ],
    "exclusions": [],
}


class _OkParser:
    """Имитация парсера, отдающего закупку (дальше скоринг)."""

    async def get_procurement(self, procurement_id: int) -> dict:
        return {"id": procurement_id, "score": 0.5}

    async def get_active_client(
        self, internal_token: str | None = None, profile_id: int | None = None
    ) -> dict:
        # Структурированный профиль (схема Profile, BR-07): profile_to_texts принимает
        # только структурированное значение, свободный текст/legacy-markdown не поддерживается.
        return {"competencies": _PROFILE}

    async def get_scoring_config(self, internal_token: str | None = None) -> dict:
        # Без аналитических переопределений: scorer собирается из базовых настроек.
        return {}


class _NoCompetenciesParser(_OkParser):
    """Имитация парсера без компетенций активного профиля (скоринг невозможен)."""

    async def get_active_client(
        self, internal_token: str | None = None, profile_id: int | None = None
    ) -> dict:
        return {"competencies": ""}


class _AnalystConfigParser(_OkParser):
    """Имитация парсера, отдающего аналитические скор-настройки."""

    async def get_scoring_config(self, internal_token: str | None = None) -> dict:
        return {"embedding_filter_threshold": 0.5, "giga_embedding_alpha": 0.25}


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


class _SuccessScorer:
    """Имитация успешного скоринга (без LLM)."""

    def score(
        self,
        record: dict[str, Any],
        competencies: str,
        procurement_id: int | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_name: str = "scoring_job",
    ) -> object:
        return SimpleNamespace(
            score=1.0,
            fit_multiplier=1.0,
            score_method="fit",
            embedding_similarity=None,
            langfuse_trace_url=None,
        )


@pytest.fixture
async def worker_queue():
    settings = Settings(
        parser_retry_backoff_seconds=0.0,
        llm_retry_backoff_seconds=0.0,
    )
    worker = ScoringWorker(settings)
    worker._queue._client = FakeRedis(server=FakeServer(), decode_responses=True)
    # По умолчанию — фейковый scorer, чтобы тесты не строили реальный LLM-пайплайн.
    worker._scorer = _SuccessScorer()
    worker._scoring_snapshot = "base"
    yield worker
    await worker._queue.close()


async def test_transient_parser_error_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _UnreachableParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(133, 27100.0, profile_id=1)
    await worker._process_once()
    # Задача должна вернуться в очередь с прежним приоритетом.
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:133:pf:1")
    assert score == 27100.0
    # И быть снятой с обработки.
    processing = worker._queue._settings.processing_key
    assert await worker._queue._client.zscore(processing, "proc:133:pf:1") is None


async def test_http_500_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _InternalErrorParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(5, 100.0, profile_id=1)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:5:pf:1")
    assert score == 100.0


async def test_http_404_drops_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _MissingParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(7, 50.0, profile_id=1)
    await worker._process_once()
    # 404 — закупка удалена у парсера: задача снимается навсегда.
    assert (
        await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:7:pf:1") is None
    )


async def test_missing_competencies_drops_job_without_file_fallback(worker_queue) -> None:
    """Без компетенций активного профиля закупка не скорится файловым профилем."""
    worker = worker_queue
    worker._parser = _NoCompetenciesParser()
    assert worker._queue._client is not None
    await worker._queue.enqueue(77, 1.0, profile_id=1)
    await worker._process_once()
    # Компетенции не заданы: задача снимается, файловый профиль-фallback НЕ используется.
    assert (
        await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:77:pf:1") is None
    )
    results = worker._queue._settings.results_key
    assert await worker._queue._client.llen(results) == 0


async def test_scorer_applies_analyst_scoring_config(
    worker_queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scorer собирается под аналитические скор-настройки из парсера."""
    worker = worker_queue
    worker._parser = _AnalystConfigParser()

    def _fake_build_scorer(settings):
        return SimpleNamespace(_settings=settings, score=lambda *a, **k: None)

    # Заглушку убрали: подменяем сборку scorer, чтобы не строить реальный LLM-пайплайн.
    monkeypatch.setattr("scoring_service.worker.build_scorer", _fake_build_scorer)
    assert worker._queue._client is not None
    scorer = await worker._ensure_scorer()
    assert scorer._settings.embedding_filter_threshold == 0.5
    assert scorer._settings.giga_embedding_alpha == 0.25
    # Тот же snapshot — scorer не пересобирается.
    assert await worker._ensure_scorer() is scorer


async def test_llm_timeout_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    worker._scorer = _TimeoutScorer()
    # Snapshot «base» (пустые переопределения): _ensure_scorer не пересобирает fake.
    worker._scoring_snapshot = "base"
    assert worker._queue._client is not None
    await worker._queue.enqueue(306, 1781072448.0, profile_id=1)
    await worker._process_once()
    # Задача должна вернуться в очередь с прежним приоритетом (не потеряться).
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:306:pf:1")
    assert score == 1781072448.0
    # И счётчик ретраев инкрементирован.
    retries = await worker._queue._client.hget(
        worker._queue._settings.jobs_retry_key, "proc:306:pf:1"
    )
    assert retries == "1"
    # И быть снятой с обработки.
    processing = worker._queue._settings.processing_key
    assert await worker._queue._client.zscore(processing, "proc:306:pf:1") is None


async def test_llm_timeout_dropped_after_max_attempts(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    worker._scorer = _TimeoutScorer()
    worker._scoring_snapshot = "base"
    assert worker._queue._client is not None
    # До обработки уже было llm_retry_max_attempts неудач.
    for _ in range(worker._settings.llm_retry_max_attempts):
        await worker._queue.increment_retries(306, 1)
    await worker._queue.enqueue(306, 1.0, profile_id=1)
    await worker._process_once()
    # Лимит исчерпан: задача снимается навсегда, счётчик обнулён.
    assert (
        await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:306:pf:1")
        is None
    )
    retries = await worker._queue._client.hget(
        worker._queue._settings.jobs_retry_key, "proc:306:pf:1"
    )
    assert retries is None


async def test_llm_rejection_drops_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    worker._scorer = _RejectedScorer()
    worker._scoring_snapshot = "base"
    assert worker._queue._client is not None
    await worker._queue.enqueue(400, 1.0, profile_id=1)
    await worker._process_once()
    # 4xx — постоянная ошибка: задача снимается навсегда без ретраев.
    assert (
        await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:400:pf:1")
        is None
    )
    retries = await worker._queue._client.hget(
        worker._queue._settings.jobs_retry_key, "proc:400:pf:1"
    )
    assert retries is None


async def test_success_resets_retries(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _OkParser()
    # Фейковый scorer из фикстуры: успех без реального LLM-пайплайна.
    assert worker._queue._client is not None
    # Были сбои LLM, потом успех — счётчик обнуляется.
    for _ in range(2):
        await worker._queue.increment_retries(9, 1)
    await worker._queue.enqueue(9, 1.0, profile_id=1)
    await worker._process_once()
    retries = await worker._queue._client.hget(
        worker._queue._settings.jobs_retry_key, "proc:9:pf:1"
    )
    assert retries is None
    # И результат опубликован.
    results = worker._queue._settings.results_key
    assert await worker._queue._client.llen(results) == 1
