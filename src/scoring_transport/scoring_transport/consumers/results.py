"""Потребитель результатов скоринга: возврат в парсер через ``POST /score``.

Цикл: ``BRPOP scoring:results`` → ``POST /api/procurements/{id}/score`` с ретраями.
"""

from __future__ import annotations

import asyncio
import logging

from scoring_common.parser_api import ParserApiClient
from scoring_transport.broker.redis_queue import TransportQueue
from scoring_transport.settings import Settings

logger = logging.getLogger(__name__)


class ResultsConsumer:
    """Возвращает результаты скоринга в парсер."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue = TransportQueue(settings)
        self._parser = ParserApiClient(
            settings.parser_api_url, internal_token=settings.parser_internal_token
        )

    async def run_forever(self) -> None:
        await self._queue.connect()
        logger.info("Results consumer started")
        try:
            while True:
                try:
                    await self._process_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Ошибка в цикле консьюмера результатов: %s", exc)
                    await asyncio.sleep(1.0)
        finally:
            await self._queue.close()

    async def _process_once(self) -> None:
        payload = await self._queue.pop_result()
        if payload is None:
            return
        procurement_id = payload.get("procurement_id")
        score = payload.get("score")
        if procurement_id is None:
            logger.warning("Пропускаю некорректный результат: %s", payload)
            return
        # Результат анализа (RAG-отчёт) не обязан нести score: score_method не меняется.
        if score is None and "rag_report" not in payload:
            logger.warning("Пропускаю некорректный результат: %s", payload)
            return
        try:
            cost_usd = payload.get("cost_usd")
            score_costs = {"usd": cost_usd} if cost_usd is not None else None
            await self._parser.post_score(
                int(procurement_id),
                float(score or 0.0),
                payload.get("score_method", "fit"),
                fit_score=payload.get("fit_score"),
                embedding_similarity=payload.get("embedding_similarity"),
                langfuse_trace_url=payload.get("langfuse_trace_url"),
                p_win=payload.get("p_win"),
                margin=payload.get("margin"),
                rag_report=payload.get("rag_report"),
                score_costs=score_costs,
                profile_id=payload.get("profile_id"),
                retry_max=self._settings.retry_max,
                retry_backoff=self._settings.retry_backoff_seconds,
                internal_token=self._settings.parser_internal_token,
            )
            logger.info("Score для закупки %s отправлен в парсер: %s", procurement_id, score)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Не удалось вернуть score для %s после всех ретраев: %s",
                procurement_id,
                exc,
            )


async def run_consumer(settings: Settings) -> None:
    consumer = ResultsConsumer(settings)
    await consumer.run_forever()
