"""Общий цикл обработки задания стадии каскада скоринга.

Воркеры pwin_service и margin_service используют один и тот же retry/recovery-цикл
(claim → карточка → расчёт → publish; requeue на 5xx/транспортных ошибках, снятие
на 4xx). Логика вынесена сюда, чтобы поведение стадий не расходилось.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from scoring_common.parser_api import ParserApiClient
from scoring_common.queue import StageQueue

logger = logging.getLogger(__name__)


async def process_stage_job(
    queue: StageQueue,
    parser: ParserApiClient,
    procurement_id: int,
    priority: float,
    *,
    retry_backoff_seconds: float,
    compute: Callable[[dict[str, Any], int], Awaitable[dict[str, Any]]]
    | Callable[[dict[str, Any], int], dict[str, Any]],
) -> None:
    """Обработать одно задание стадии каскада.

    ``compute(record, procurement_id)`` — стадие-специфичный расчёт: возвращает
    payload результата (содержит ``procurement_id``, ``score``, ``score_method`` и
    компоненты стадии). Ошибки парсера: HTTP 5xx/транспортные → задача возвращается
    в очередь с прежним приоритетом (best-effort, как в fit-воркере); HTTP 4xx
    (404 — закупка удалена) → задача снимается навсегда.
    """
    try:
        await queue.claim_processing(procurement_id, priority)
        record = await parser.get_procurement(procurement_id)
        result = compute(record, procurement_id)
        payload = await result if inspect.iscoroutine(result) else result
        assert isinstance(payload, dict)
        await queue.publish_result(payload)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            logger.warning(
                "Парсер ответил HTTP %s для закупки %s — задача возвращена в очередь",
                exc.response.status_code,
                procurement_id,
            )
            await queue.enqueue(procurement_id, priority)
            await asyncio.sleep(retry_backoff_seconds)
        else:
            logger.warning(
                "Парсер не нашёл закупку %s (HTTP %s) — задача снята с очереди",
                procurement_id,
                exc.response.status_code,
            )
    except httpx.TransportError as exc:
        logger.warning(
            "Парсер недоступен для закупки %s — задача возвращена в очередь: %s",
            procurement_id,
            exc,
        )
        await queue.enqueue(procurement_id, priority)
        await asyncio.sleep(retry_backoff_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Обработка стадии для %s упала: %s", procurement_id, exc)
    finally:
        await queue.finish_processing(procurement_id)
