"""Фоновый воркер: потребляет задачи из Redis-очереди и скорит закупки.

Цикл: ``ZPOPMAX scoring:jobs`` (наибольший приоритет первым) → получить карточку
из парсера через REST → прогнать пайплайн → ``LPUSH scoring:results``.
Очередь и клиент парсера — общие (scoring_common).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx
import openai

from scoring_common.parser_api import ParserApiClient
from scoring_common.queue import StageQueue
from scoring_service.profile import ProfileTexts
from scoring_service.scoring import Scorer, build_scorer
from scoring_service.settings import Settings, apply_scoring_overrides

logger = logging.getLogger(__name__)


class ScoringWorker:
    """Воркер обработки задач из очереди."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Один run_id на всё время жизни воркера: все обработанные им задания
        # объединяются в одну LangFuse-сессию (одни гиперпараметры/промпты).
        self._run_id = uuid.uuid4().hex
        # Скорer строится отложенно из актуальных аналитических скор-настроек
        # (см. _ensure_scorer): при изменении config_service.yaml -> scoring парсер
        # отдаёт новый snapshot, воркер пересобирает scorer без рестарта.
        self._scorer: Scorer | None = None
        self._scoring_snapshot: str | None = None
        self._queue = StageQueue(settings)
        self._parser = ParserApiClient(
            settings.parser_api_url, internal_token=settings.parser_internal_token
        )
        # Кэш нормализации профиля активного клиента: значение (str/dict) → ProfileTexts.
        # Профиль постоянен в рамках жизни воркера — не пересобираем его на каждую закупку.
        # Fallback на конкретный профиль из файла НЕ используется: компетенции берутся
        # только из активного профиля клиента (парсер). Если профиль недоступен —
        # задача обрабатывается как ошибка (ретрай/снятие), но не скорится «чужим» профилем.
        self._profile_cache: tuple[object, ProfileTexts] | None = None

    async def _resolve_competencies(self, profile_id: int) -> ProfileTexts:
        """Компетенции профиля (из парсера). Без fallback на файл.

        Профиль известен из задания очереди (пер-профильно, BR-07): скоринг
        считается по компетенциям именно этого профиля. Парсер может отдать
        структурированный профиль (dict/YAML) или текст; нормализуем в пару
        ``ProfileTexts`` (llm/embedding) через ``profile_to_texts``. Ошибки
        ``httpx.HTTPStatusError``/``httpx.TransportError`` прокидываются наверх —
        там они обрабатываются как сбой парсера (ретрай/снятие). Если компетенции
        не удалось извлечь — поднимаем ошибку: закупка не скорится без контекста.
        """
        from scoring_service.profile import profile_to_texts

        client = await self._parser.get_active_client(
            internal_token=self._settings.parser_internal_token, profile_id=profile_id
        )
        raw = (client or {}).get("competencies")
        if self._profile_cache is not None and self._profile_cache[0] == (profile_id, raw):
            return self._profile_cache[1]
        texts = profile_to_texts(raw)
        if texts is None or not texts.llm:
            raise RuntimeError("Компетенции профиля не заданы — скоринг без контекста невозможен")
        self._profile_cache = ((profile_id, raw), texts)
        return texts

    def _scoring_snapshot_key(self, snapshot: dict[str, Any] | None) -> str:
        """Ключ сравнения скор-настроек: пересобираем scorer только при изменении."""
        if not snapshot:
            return "base"
        return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)

    async def _ensure_scorer(self) -> Scorer:
        """Вернуть scorer, собранный под актуальные аналитические скор-настройки.

        Snapshot берётся из парсера (``/api/config/scoring``, TTL-кэш). При изменении
        конфигурации scorer пересобирается. Ошибки парсера прокидываются наверх —
        там обрабатываются как сбой парсера (ретрай/снятие).
        """
        snapshot = await self._parser.get_scoring_config(
            internal_token=self._settings.parser_internal_token
        )
        key = self._scoring_snapshot_key(snapshot)
        if self._scorer is not None and key == self._scoring_snapshot:
            return self._scorer
        effective = apply_scoring_overrides(self._settings, snapshot)
        self._scorer = build_scorer(effective)
        self._scoring_snapshot = key
        logger.info(
            "scorer построен под скор-настройки (filter_threshold=%s, alpha=%s, refine=%s)",
            effective.embedding_filter_threshold,
            effective.giga_embedding_alpha,
            effective.num_refine_rounds,
        )
        return self._scorer

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
        procurement_id, profile_id, priority = job
        logger.info(
            "Processing procurement %s (profile %s, priority=%.2f)",
            procurement_id,
            profile_id,
            priority,
        )
        try:
            await self._queue.claim_processing(procurement_id, profile_id, priority)
            record = await self._parser.get_procurement(procurement_id)
            competencies = await self._resolve_competencies(profile_id)
            scorer = await self._ensure_scorer()
            result = scorer.score(record, competencies, procurement_id, run_id=self._run_id)
            await self._queue.publish_result(
                {
                    "procurement_id": procurement_id,
                    "profile_id": profile_id,
                    "score": result.score,
                    "fit_score": result.fit_multiplier,
                    "score_method": result.score_method,
                    "embedding_similarity": result.embedding_similarity,
                    "langfuse_trace_url": result.langfuse_trace_url,
                }
            )
            # Успех: обнуляем счётчик ретраев (если до этого были сбои LLM).
            await self._queue.reset_retries(procurement_id, profile_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                logger.warning(
                    "Парсер ответил HTTP %s для закупки %s — задача возвращена в очередь",
                    exc.response.status_code,
                    procurement_id,
                )
                await self._queue.enqueue(procurement_id, priority, profile_id)
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
            await self._queue.enqueue(procurement_id, priority, profile_id)
            await asyncio.sleep(self._settings.parser_retry_backoff_seconds)
        except openai.APIConnectionError as exc:
            # LLM-провайдер недоступен или не ответил в llm_request_timeout
            # (openai.APITimeoutError — подкласс): возвращаем задачу в очередь
            # с backoff, чтобы закупка не потерялась. Иначе она упала бы в
            # общий except и была бы снята навсегда (см. лог «Request timed out»).
            await self._retry_llm_or_drop(
                procurement_id,
                profile_id,
                priority,
                f"LLM-провайдер недоступен: {exc}",
            )
        except openai.APIStatusError as exc:
            # HTTP-ошибка провайдера: 429/5xx — транзиентная, возвращаем в очередь;
            # 4xx (неверный запрос/ключ) — постоянная, задача снимается.
            if exc.status_code >= 500 or isinstance(exc, openai.RateLimitError):
                await self._retry_llm_or_drop(
                    procurement_id,
                    profile_id,
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
            await self._queue.finish_processing(procurement_id, profile_id)

    async def _retry_llm_or_drop(
        self, procurement_id: int, profile_id: int, priority: float, reason: str
    ) -> None:
        """Вернуть задачу в очередь при транзиентном сбое LLM (с лимитом попыток).

        Счётчик ретраев хранится в Redis (``jobs_retry_key``): после
        ``llm_retry_max_attempts`` неудач подряд задача снимается навсегда, чтобы
        не крутить её вечно при стабильном падении провайдера.
        """
        retries = await self._queue.increment_retries(procurement_id, profile_id)
        if retries > self._settings.llm_retry_max_attempts:
            logger.error(
                "LLM-провайдер стабильно недоступен для закупки %s после %d попыток "
                "— задача снята с очереди: %s",
                procurement_id,
                retries,
                reason,
            )
            await self._queue.reset_retries(procurement_id, profile_id)
            return
        logger.warning(
            "Закупка %s возвращена в очередь из-за сбоя LLM (попытка %d/%d): %s",
            procurement_id,
            retries,
            self._settings.llm_retry_max_attempts,
            reason,
        )
        await self._queue.enqueue(procurement_id, priority, profile_id)
        await asyncio.sleep(self._settings.llm_retry_backoff_seconds)


async def run_worker(settings: Settings) -> None:
    worker = ScoringWorker(settings)
    await worker.run_forever()
