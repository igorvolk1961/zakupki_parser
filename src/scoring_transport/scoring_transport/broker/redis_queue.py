"""Redis-очередь со стороны транспорта.

- Постановка задач: ``ZADD scoring:jobs`` (score = приоритет = дефолтный score).
- Потребление результатов: ``BRPOP scoring:results``.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
import redis.exceptions as redis_exceptions

from scoring_transport.settings import Settings


class TransportQueue:
    """Очередь задач и результатов на Redis (сторона транспорта)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self._settings.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def enqueue(self, procurement_id: int, priority: float, stage: str = "fit") -> None:
        """Постановка задачи с приоритетом = дефолтным score.

        ``stage`` — стадия каскада (fit/pwin/margin): определяет Redis-очередь задач.
        """
        assert self._client is not None
        key = self._jobs_key_for(stage)
        await self._client.zadd(key, {f"proc:{procurement_id}": priority})

    def _jobs_key_for(self, stage: str) -> str:
        if stage == "pwin":
            return self._settings.pwin_jobs_key
        if stage == "margin":
            return self._settings.margin_jobs_key
        return self._settings.jobs_key

    def _results_keys(self) -> list[str]:
        return [
            self._settings.results_key,
            self._settings.pwin_results_key,
            self._settings.margin_results_key,
        ]

    async def pop_result(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Взять результат (BRPOP), вернуть payload или None.

        Слушает очереди результатов всех стадий каскада (scoring/pwin/margin).
        Блокирующий ``BRPOP`` может превысить таймаут сокета Redis-клиента
        (``TimeoutError``) — это не ошибка доставки, а «результата ещё нет»:
        возвращаем None, чтобы консьюмер продолжал цикл.
        """
        assert self._client is not None
        t = self._settings.result_timeout_seconds if timeout is None else timeout
        try:
            result = await self._client.brpop(self._results_keys(), timeout=t)
        except redis_exceptions.TimeoutError:
            return None
        if result is None:
            return None
        _, payload = result
        data: dict[str, Any] = json.loads(payload)
        return data
