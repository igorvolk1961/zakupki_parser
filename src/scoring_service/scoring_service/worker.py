"""Фоновый воркер: потребляет задачи из Redis-очереди и скорит закупки.

Цикл: ``ZPOPMAX scoring:jobs`` (наибольший приоритет первым) → получить карточку
из парсера через REST → прогнать пайплайн → ``LPUSH scoring:results``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from scoring_service.scoring import build_scorer
from scoring_service.settings import Settings
from scoring_service.transport.parser_api import ParserApiClient
from scoring_service.transport.redis_queue import ScoringQueue

logger = logging.getLogger(__name__)


class ScoringWorker:
    """Воркер обработки задач из очереди."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Один run_id на всё время жизни воркера: все обработанные им задания
        # объединяются в одну LangFuse-сессию (одни гиперпараметры/промпты).
        self._run_id = uuid.uuid4().hex
        self._scorer = build_scorer(settings)
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
            result = self._scorer.score(record, competencies, procurement_id, run_id=self._run_id)
            await self._queue.publish_result(
                {
                    "procurement_id": procurement_id,
                    "score": result.score,
                    "fit_score": result.fit_multiplier,
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
