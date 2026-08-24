"""Фоновый воркер: потребляет задачи из Redis-очереди и скорит закупки.

Цикл: ``ZPOPMAX scoring:jobs`` (наибольший приоритет первым) → получить карточку
из парсера через REST → прогнать пайплайн → ``LPUSH scoring:results``.
Очередь и клиент парсера — общие (scoring_common).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
import openai

from scoring_common.parser_api import ParserApiClient
from scoring_common.queue import StageQueue
from scoring_service.scoring import build_scorer
from scoring_service.settings import Settings

logger = logging.getLogger(__name__)


class ScoringWorker:
    """Воркер обработки задач из очереди."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Один run_id на всё время жизни воркера: все обработанные им задания
        # объединяются в одну LangFuse-сессию (одни гиперпараметры/промпты).
        self._run_id = uuid.uuid4().hex
        self._scorer = build_scorer(settings)
        self._queue = StageQueue(settings)
        self._parser = ParserApiClient(
            settings.parser_api_url, internal_token=settings.parser_internal_token
        )
        self._competencies = settings.competencies()

    async def _resolve_competencies(self) -> str:
        """Компетенции активного клиентского профиля (из парсера), fallback — файл."""
        try:
            client = await self._parser.get_active_client(
                internal_token=self._settings.parser_internal_token
            )
            competencies = (client or {}).get("competencies")
            if isinstance(competencies, str) and competencies:
                return competencies
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.warning(
                "Не удалось получить активный клиентский профиль (%s) — компетенции из файла",
                exc,
            )
        return self._competencies

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
            competencies = await self._resolve_competencies()
            result = self._scorer.score(record, competencies, procurement_id, run_id=self._run_id)
            await self._queue.publish_result(
                {
                    "procurement_id": procurement_id,
                    "score": result.score,
                    "fit_score": result.fit_multiplier,
                    "score_method": result.score_method,
                    "embedding_similarity": result.embedding_similarity,
                }
            )
            # Успех: обнуляем счётчик ретраев (если до этого были сбои LLM).
            await self._queue.reset_retries(procurement_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                logger.warning(
                    "Парсер ответил HTTP %s для закупки %s — задача возвращена в очередь",
                    exc.response.status_code,
                    procurement_id,
                )
                await self._queue.enqueue(procurement_id, priority)
                await asyncio.sleep(self._settings.parser_retry_backoff_seconds)
            else:
                logger.warning(
                    "Парсер не нашёл закупку %s (HTTP %s) — задача снята с очереди",
                    procurement_id,
                    exc.response.status_code,
                )
        except httpx.TransportError as exc:
            # Парсер временно недоступен (ещё не запущен/перезапускается):
            # возвращаем задачу в очередь и пробуем снова позже, чтобы закупка
            # не потерялась. Задача уже снята с ZSET при pop_job, поэтому здесь
            # явный requeue с прежним приоритетом.
            logger.warning(
                "Парсер недоступен для закупки %s — задача возвращена в очередь: %s",
                procurement_id,
                exc,
            )
            await self._queue.enqueue(procurement_id, priority)
            await asyncio.sleep(self._settings.parser_retry_backoff_seconds)
        except openai.APIConnectionError as exc:
            # LLM-провайдер недоступен или не ответил в llm_request_timeout
            # (openai.APITimeoutError — подкласс): возвращаем задачу в очередь
            # с backoff, чтобы закупка не потерялась. Иначе она упала бы в
            # общий except и была бы снята навсегда (см. лог «Request timed out»).
            await self._retry_llm_or_drop(
                procurement_id,
                priority,
                f"LLM-провайдер недоступен: {exc}",
            )
        except openai.APIStatusError as exc:
            # HTTP-ошибка провайдера: 429/5xx — транзиентная, возвращаем в очередь;
            # 4xx (неверный запрос/ключ) — постоянная, задача снимается.
            if exc.status_code >= 500 or isinstance(exc, openai.RateLimitError):
                await self._retry_llm_or_drop(
                    procurement_id,
                    priority,
                    f"LLM-провайдер ответил HTTP {exc.status_code}",
                )
            else:
                logger.warning(
                    "LLM-провайдер отклонил запрос для закупки %s (HTTP %s) — "
                    "задача снята с очереди",
                    procurement_id,
                    exc.status_code,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scoring failed for %s: %s", procurement_id, exc)
        finally:
            await self._queue.finish_processing(procurement_id)

    async def _retry_llm_or_drop(self, procurement_id: int, priority: float, reason: str) -> None:
        """Вернуть задачу в очередь при транзиентном сбое LLM (с лимитом попыток).

        Счётчик ретраев хранится в Redis (``jobs_retry_key``): после
        ``llm_retry_max_attempts`` неудач подряд задача снимается навсегда, чтобы
        не крутить её вечно при стабильном падении провайдера.
        """
        retries = await self._queue.increment_retries(procurement_id)
        if retries > self._settings.llm_retry_max_attempts:
            logger.error(
                "LLM-провайдер стабильно недоступен для закупки %s после %d попыток "
                "— задача снята с очереди: %s",
                procurement_id,
                retries,
                reason,
            )
            await self._queue.reset_retries(procurement_id)
            return
        logger.warning(
            "Закупка %s возвращена в очередь из-за сбоя LLM (попытка %d/%d): %s",
            procurement_id,
            retries,
            self._settings.llm_retry_max_attempts,
            reason,
        )
        await self._queue.enqueue(procurement_id, priority)
        await asyncio.sleep(self._settings.llm_retry_backoff_seconds)


async def run_worker(settings: Settings) -> None:
    worker = ScoringWorker(settings)
    await worker.run_forever()
