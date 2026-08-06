"""Redis-очередь скоринга (sorted set по приоритету).

- ``scoring:jobs`` (ZSET): member = ``proc:{id}``, score = priority (дефолтный score).
  Потребление — ``ZPOPMAX``: сначала закупки с наибольшим дефолтным score.
- ``scoring:results`` (LIST): результаты скоринга (JSON), ``LPUSH`` producer / ``BRPOP`` consumer.
- ``scoring:processing`` (ZSET): аренда в обработке с TTL для восстановления после сбоя.
"""

from __future__ import annotations

import json
import time
from typing import Any, cast

import redis.asyncio as aioredis

from scoring_service.settings import Settings


class ScoringQueue:
    """Очередь задач и результатов на Redis."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self._settings.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _member(self, procurement_id: int) -> str:
        return f"proc:{procurement_id}"

    async def enqueue(self, procurement_id: int, priority: float) -> None:
        """Постановка задачи с приоритетом = дефолтным score."""
        assert self._client is not None
        await self._client.zadd(self._settings.jobs_key, {self._member(procurement_id): priority})

    async def pop_job(self) -> tuple[int, float] | None:
        """Взять задачу с наибольшим приоритетом (ZPOPMAX)."""
        assert self._client is not None
        result = await self._client.zpopmax(self._settings.jobs_key, count=1)
        if not result:
            return None
        member: str = cast(str, result[0][0])
        score: float = cast(float, result[0][1])
        return int(member.split(":", 1)[1]), float(score)

    async def claim_processing(self, procurement_id: int, priority: float) -> None:
        """Пометить задачу как «в обработке»: TTL-аренда + сохранение приоритета."""
        assert self._client is not None
        member = self._member(procurement_id)
        await self._client.zadd(self._settings.processing_key, {member: time.time()})
        await self._client.expire(
            self._settings.processing_key, self._settings.processing_ttl_seconds
        )
        await self._client.hset(self._settings.processing_meta_key, member, str(priority))

    async def finish_processing(self, procurement_id: int) -> None:
        assert self._client is not None
        member = self._member(procurement_id)
        await self._client.zrem(self._settings.processing_key, member)
        await self._client.hdel(self._settings.processing_meta_key, member)

    async def recover_stale(self) -> None:
        """Вернуть «зависшие» задачи (аренда истекла) обратно в очередь.

        Redis не гарантирует доставку: если воркер упал после pop_job, задача остаётся
        в processing-наборе. Здесь просроченные члены снова ставятся в scoring:jobs с
        сохранённым приоритетом (из processing_meta), чтобы закупка не потерялась
        (скоринг идемпотентен через POST /score).
        """
        assert self._client is not None
        cutoff = time.time() - self._settings.processing_ttl_seconds
        stale: list[str] = cast(
            list[str],
            await self._client.zrangebyscore(self._settings.processing_key, "-inf", cutoff),
        )
        for member in stale:
            if member.startswith("proc:"):
                raw = await self._client.hget(self._settings.processing_meta_key, member)
                priority = (
                    float(raw) if raw is not None else self._settings.processing_recovery_priority
                )
                await self._client.zadd(self._settings.jobs_key, {member: priority})
            await self._client.zrem(self._settings.processing_key, member)
            await self._client.hdel(self._settings.processing_meta_key, member)

    async def publish_result(self, payload: dict[str, Any]) -> None:
        """Опубликовать результат в LIST scoring:results."""
        assert self._client is not None
        await self._client.lpush(
            self._settings.results_key, json.dumps(payload, ensure_ascii=False)
        )
