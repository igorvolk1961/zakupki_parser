"""Фоновый воркер RAG-анализа: потребляет задачи из Redis-очереди.

Цикл: ``ZPOPMAX analysis:jobs`` → карточка закупки + активный клиентский профиль
(вопросы и факты BR-03) → обязательные системные проверки (1 LLM-вызов + матчер
по фактам профиля) и вопросы клиента (эмбеддинги, LLM-вердикты) → ``LPUSH
analysis:results`` (транспорт возвращает rag_report в парсер).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from analysis_service.llm import LlmClient
from analysis_service.pipeline.rag import RagAnalyzer
from analysis_service.settings import Settings
from scoring_common.embeddings import EmbeddingClient
from scoring_common.parser_api import ParserApiClient
from scoring_common.queue import StageQueue
from scoring_common.stage_worker import process_stage_job

logger = logging.getLogger(__name__)


class AnalysisWorker:
    """Воркер обработки задач RAG-анализа."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue = StageQueue(settings)
        self._parser = ParserApiClient(
            settings.parser_api_url, internal_token=settings.parser_internal_token
        )
        self._embedder = EmbeddingClient(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            timeout=settings.embedding_timeout,
        )
        self._llm = LlmClient(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            temperature=settings.llm_temperature,
            timeout=settings.llm_request_timeout,
        )
        self._analyzer = RagAnalyzer(settings, self._embedder, self._llm)

    async def run_forever(self) -> None:
        await self._queue.connect()
        logger.info("Analysis worker started (poll %.1fs)", self._settings.queue_poll_seconds)
        try:
            while True:
                await self._queue.recover_stale()
                await self._process_once()
                await asyncio.sleep(self._settings.queue_poll_seconds)
        finally:
            await self._queue.close()

    async def _resolve_questions(self, profile_id: int) -> list[dict[str, Any]]:
        """Вопросы профиля (из парсера); None при сбое."""
        try:
            client = await self._parser.get_active_client(
                internal_token=self._settings.parser_internal_token, profile_id=profile_id
            )
            questions = (client or {}).get("questions") or []
            return [q for q in questions if isinstance(q, dict)]
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.warning("Не удалось получить вопросы клиента: %s", exc)
            return []

    async def _resolve_profile_facts(self, profile_id: int) -> dict[str, Any]:
        """Факты профиля для сопоставления с фактами ТЗ (Stage B).

        Лицензии/подтверждённый опыт приходят из ответа ``/api/clients/active``
        (тот же кэшируемый вызов, что и вопросы). При сбое — пустые факты:
        нераспознанные/неподтверждённые барьеры маркируются жёстко, не молча
        пропускаются.
        """
        try:
            client = await self._parser.get_active_client(
                internal_token=self._settings.parser_internal_token, profile_id=profile_id
            )
            facts = (client or {}).get("facts")
            if isinstance(facts, dict):
                return {
                    "license_codes": list(facts.get("license_codes") or []),
                    "experience_codes": list(facts.get("experience_codes") or []),
                }
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.warning("Не удалось получить факты профиля: %s", exc)
        return {"license_codes": [], "experience_codes": []}

    async def _process_once(self) -> None:
        job = await self._queue.pop_job()
        if job is None:
            return
        procurement_id, profile_id, priority = job
        logger.info(
            "Processing analysis for procurement %s (profile %s, priority=%.2f)",
            procurement_id,
            profile_id,
            priority,
        )

        async def compute(record: dict[str, Any], pid: int, pfd: int) -> dict[str, Any]:
            questions = await self._resolve_questions(pfd)
            profile_facts = await self._resolve_profile_facts(pfd)
            report = await self._analyzer.analyze(record, questions, profile_facts)
            return {
                "procurement_id": pid,
                "profile_id": pfd,
                "score": 0.0,
                "score_method": "fit",
                "rag_report": report,
            }

        await process_stage_job(
            self._queue,
            self._parser,
            procurement_id,
            profile_id,
            priority,
            retry_backoff_seconds=self._settings.parser_retry_backoff_seconds,
            compute=compute,
        )


async def run_worker(settings: Settings) -> None:
    worker = AnalysisWorker(settings)
    await worker.run_forever()
