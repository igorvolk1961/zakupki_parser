"""Параметризованная Redis-очередь стадии каскада скоринга.

Обобщение ``ScoringQueue`` (scoring_service) и ``TransportQueue`` (scoring_transport):
ключи (задачи/результаты/аренда) берутся из настроек сервиса, поэтому один класс
используется в ``pwin_service`` и ``margin_service``.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol, cast

import redis.asyncio as aioredis


class StageQueueSettings(Protocol):
    """Минимальный набор настроек очереди (утиная типизация)."""

    redis_url: str
    jobs_key: str
    results_key: str
    processing_key: str
    processing_meta_key: str
    processing_ttl_seconds: int
    processing_recovery_priority: float
    queue_poll_seconds: float
    # Счётчик ретраев задач (HASH member -> int): ограничивает число возвратов
    # в очередь при транзиентных сбоях стадии (например, таймаут LLM-провайдера).
    jobs_retry_key: str


class StageQueue:
    """Очередь задач и результатов одной стадии на Redis (ZSET + LIST)."""

    def __init__(self, settings: StageQueueSettings) -> None:
        self._settings = settings
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self._settings.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _member(self, procurement_id: int, profile_id: int) -> str:
        return f"proc:{procurement_id}:pf:{profile_id}"

    @staticmethod
    def _parse_member(member: str) -> tuple[int, int] | None:
        """Разобрать ``proc:{id}:pf:{profile_id}`` → ``(procurement_id, profile_id)``."""
        try:
            body, profile_part = member.split(":pf:", 1)
            procurement_id = int(body.split(":", 1)[1])
            profile_id = int(profile_part)
        except (ValueError, IndexError):
            return None
        return procurement_id, profile_id

    async def enqueue(self, procurement_id: int, priority: float, profile_id: int) -> None:
        """Постановка задачи с приоритетом = накопленным значением.

        ``profile_id`` — профиль, по компетенциям которого считается скор
        (per-profile, BR-07): результат стадии пишется именно этому профилю.
        """
        assert self._client is not None
        member = self._member(procurement_id, profile_id)
        await self._client.zadd(self._settings.jobs_key, {member: priority})

    async def pop_job(self) -> tuple[int, int, float] | None:
        """Взять задачу с наибольшим приоритетом (ZPOPMAX).

        Возвращает ``(procurement_id, profile_id, priority)``.
        """
        assert self._client is not None
        result = await self._client.zpopmax(self._settings.jobs_key, count=1)
        if not result:
            return None
        member: str = cast(str, result[0][0])
        score: float = cast(float, result[0][1])
        parsed = self._parse_member(member)
        if parsed is None:
            raise ValueError(f"Неизвестный формат задания очереди: {member!r}")
        procurement_id, profile_id = parsed
        return procurement_id, profile_id, float(score)

    async def claim_processing(self, procurement_id: int, profile_id: int, priority: float) -> None:
        """Пометить задачу как «в обработке»: TTL-аренда + сохранение приоритета."""
        assert self._client is not None
        member = self._member(procurement_id, profile_id)
        await self._client.zadd(self._settings.processing_key, {member: time.time()})
        await self._client.expire(
            self._settings.processing_key, self._settings.processing_ttl_seconds
        )
        await self._client.hset(self._settings.processing_meta_key, member, str(priority))

    async def finish_processing(self, procurement_id: int, profile_id: int) -> None:
        assert self._client is not None
        member = self._member(procurement_id, profile_id)
        await self._client.zrem(self._settings.processing_key, member)
        await self._client.hdel(self._settings.processing_meta_key, member)

    async def recover_stale(self) -> None:
        """Вернуть «зависшие» задачи (аренда истекла) обратно в очередь.

        Redis не гарантирует доставку: если воркер упал после pop_job, задача остаётся
        в processing-наборе. Здесь просроченные члены снова ставятся в jobs-очередь с
        сохранённым приоритетом (из processing_meta), чтобы закупка не потерялась.
        Профиль восстанавливается изmember (профиль — часть ключа задачи).
        """
        assert self._client is not None
        cutoff = time.time() - self._settings.processing_ttl_seconds
        stale: list[str] = cast(
            list[str],
            await self._client.zrangebyscore(self._settings.processing_key, "-inf", cutoff),
        )
        for member in stale:
            parsed = self._parse_member(member)
            if parsed is None:
                await self._client.zrem(self._settings.processing_key, member)
                await self._client.hdel(self._settings.processing_meta_key, member)
                continue
            procurement_id, profile_id = parsed
            raw = await self._client.hget(self._settings.processing_meta_key, member)
            priority = (
                float(raw) if raw is not None else self._settings.processing_recovery_priority
            )
            await self._client.zadd(self._settings.jobs_key, {member: priority})
            await self._client.zrem(self._settings.processing_key, member)
            await self._client.hdel(self._settings.processing_meta_key, member)

    async def publish_result(self, payload: dict[str, Any]) -> None:
        """Опубликовать результат в LIST results_key."""
        assert self._client is not None
        await self._client.lpush(
            self._settings.results_key, json.dumps(payload, ensure_ascii=False)
        )

    async def increment_retries(self, procurement_id: int, profile_id: int) -> int:
        """Инкремент счётчика ретраев задачи; возвращает новое значение."""
        assert self._client is not None
        member = self._member(procurement_id, profile_id)
        return int(await self._client.hincrby(self._settings.jobs_retry_key, member, 1))

    async def reset_retries(self, procurement_id: int, profile_id: int) -> None:
        """Обнулить счётчик ретраев задачи (успех либо окончательный сброс)."""
        assert self._client is not None
        member = self._member(procurement_id, profile_id)
        await self._client.hdel(self._settings.jobs_retry_key, member)
