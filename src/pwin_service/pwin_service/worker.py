"""Фоновый воркер стадии P(win): потребляет задачи из Redis-очереди.

Цикл: ``ZPOPMAX pwin:jobs`` → получить карточку из парсера через REST → расчёт
``P(win)`` → ``LPUSH pwin:results`` (транспорт возвращает результат в парсер).
Retry/recovery-логика — общая (scoring_common.stage_worker).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pwin_service.settings import Settings
from scoring_common.parser_api import ParserApiClient
from scoring_common.pwin import compute_pwin
from scoring_common.queue import StageQueue
from scoring_common.stage_worker import process_stage_job

logger = logging.getLogger(__name__)


class PwinWorker:
    """Воркер обработки задач стадии P(win)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue = StageQueue(settings)
        self._parser = ParserApiClient(settings.parser_api_url)

    async def run_forever(self) -> None:
        await self._queue.connect()
        logger.info("P(win) worker started (poll %.1fs)", self._settings.queue_poll_seconds)
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
        logger.info(
            "Processing P(win) for procurement %s (priority=%.2f)",
            procurement_id,
            priority,
        )
        await process_stage_job(
            self._queue,
            self._parser,
            procurement_id,
            priority,
            retry_backoff_seconds=self._settings.parser_retry_backoff_seconds,
            compute=self._compute_payload,
        )

    def _compute_payload(self, record: dict[str, Any], procurement_id: int) -> dict[str, Any]:
        """Расчёт P(win) и накопленного score = fit × p_win."""
        p_win = compute_pwin(record, self._settings)
        fit_score = float(record.get("fit_score") or 0.0)
        score = round(fit_score * p_win, self._settings.score_round_digits)
        return {
            "procurement_id": procurement_id,
            "score": score,
            "fit_score": fit_score,
            "p_win": p_win,
            "score_method": "pwin",
        }


async def run_worker(settings: Settings) -> None:
    worker = PwinWorker(settings)
    await worker.run_forever()
