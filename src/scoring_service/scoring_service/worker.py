"""Фоновый воркер: потребляет задачи из Redis-очереди и скорит закупки.

Цикл: ``ZPOPMAX scoring:jobs`` (наибольший приоритет первым) → получить карточку
из парсера через REST → прогнать пайплайн → ``LPUSH scoring:results``.
"""

from __future__ import annotations

import asyncio
import logging

from scoring_service.llm_factory import build_llm, callbacks_for, langfuse_handler
from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.pipeline.judge_chain import JudgeChain
from scoring_service.scoring import Scorer
from scoring_service.settings import Settings
from scoring_service.transport.parser_api import ParserApiClient
from scoring_service.transport.redis_queue import ScoringQueue

logger = logging.getLogger(__name__)


class ScoringWorker:
    """Воркер обработки задач из очереди."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        llm = build_llm(settings)
        handler = langfuse_handler(settings)
        callbacks = callbacks_for(handler)
        self._scorer = Scorer(
            FitChain(llm, callbacks),
            JudgeChain(llm, callbacks),
            settings,
        )
        self._queue = ScoringQueue(settings)
        self._parser = ParserApiClient(settings.parser_api_url)

    async def run_forever(self) -> None:
        await self._queue.connect()
        logger.info("Scoring worker started (poll %.1fs)", self._settings.queue_poll_seconds)
        try:
            while True:
                await self._queue.recover_stale()
                await self._process_once()
                await asyncio.sleep(self._settings.queue_poll_seconds)
        finally:
            await self._queue.close()

    async def _process_once(self) -> None:
        job = await self._queue.pop_job()
        if job is None:
            return
        procurement_id, priority = job
        logger.info("Processing procurement %s (priority=%.2f)", procurement_id, priority)
        try:
            await self._queue.claim_processing(procurement_id, priority)
            record = await self._parser.get_procurement(procurement_id)
            competencies = self._settings.competencies()
            result = self._scorer.score(record, competencies, procurement_id)
            await self._queue.publish_result(
                {
                    "procurement_id": procurement_id,
                    "score": result.score,
                    "fit_score": result.final_fit_score,
                    "p_win": result.p_win,
                    "margin": result.margin,
                    "score_method": "external",
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scoring failed for %s: %s", procurement_id, exc)
        finally:
            await self._queue.finish_processing(procurement_id)


async def run_worker(settings: Settings) -> None:
    worker = ScoringWorker(settings)
    await worker.run_forever()
