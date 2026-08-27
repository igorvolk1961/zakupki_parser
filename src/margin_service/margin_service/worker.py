"""Фоновый воркер стадии Margin: потребляет задачи из Redis-очереди.

Цикл: ``ZPOPMAX margin:jobs`` → получить карточку из парсера через REST → расчёт
``Margin = НМЦК × margin_rate`` → ``LPUSH margin:results``.
Retry/recovery-логика — общая (scoring_common.stage_worker).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from margin_service.settings import Settings
from scoring_common.margin import compute_margin
from scoring_common.parser_api import ParserApiClient
from scoring_common.queue import StageQueue
from scoring_common.stage_worker import process_stage_job

logger = logging.getLogger(__name__)


class MarginWorker:
    """Воркер обработки задач стадии Margin."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue = StageQueue(settings)
        self._parser = ParserApiClient(
            settings.parser_api_url, internal_token=settings.parser_internal_token
        )

    async def run_forever(self) -> None:
        await self._queue.connect()
        logger.info("Margin worker started (poll %.1fs)", self._settings.queue_poll_seconds)
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
        procurement_id, profile_id, priority = job
        logger.info(
            "Processing Margin for procurement %s (profile %s, priority=%.2f)",
            procurement_id,
            profile_id,
            priority,
        )
        await process_stage_job(
            self._queue,
            self._parser,
            procurement_id,
            profile_id,
            priority,
            retry_backoff_seconds=self._settings.parser_retry_backoff_seconds,
            compute=self._compute_payload,
        )

    def _compute_payload(
        self, record: dict[str, Any], procurement_id: int, profile_id: int
    ) -> dict[str, Any]:
        """Расчёт Margin и накопленного score = fit × p_win × margin."""
        margin = compute_margin(record, self._settings.margin_rate)
        fit_score = float(record.get("fit_score") or 0.0)
        p_win = float(record.get("p_win") or 0.0)
        score = round(fit_score * p_win * margin, self._settings.score_round_digits)
        return {
            "procurement_id": procurement_id,
            "profile_id": profile_id,
            "score": score,
            "fit_score": fit_score,
            "p_win": p_win,
            "margin": margin,
            "score_method": "margin",
        }


async def run_worker(settings: Settings) -> None:
    worker = MarginWorker(settings)
    await worker.run_forever()
