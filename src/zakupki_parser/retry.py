"""Ретраи с экспоненциальным бэкоффом и джиттером для сетевых/браузерных операций."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import RetryConfig

logger = logging.getLogger(__name__)


async def run_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    retry: RetryConfig,
    circuit: CircuitBreaker | None = None,
    label: str = "Операция",
) -> T:
    """Выполняет ``operation`` с экспоненциальным бэкоффом и джиттером.

    Каждая неудачная попытка учитывается в circuit breaker'е (если передан);
    успех сбрасывает счётчик ошибок. Если CB перешёл в OPEN — поднимается
    ``CircuitOpenError`` без дальнейших попыток.
    """
    attempt = 0
    while True:
        attempt += 1
        if circuit is not None and not circuit.allow_request():
            raise CircuitOpenError(f"{label}: сайт недоступен (circuit open)")
        try:
            result = await operation()
            if circuit is not None:
                circuit.record_success()
            return result
        except Exception as exc:  # noqa: BLE001
            if circuit is not None:
                circuit.record_failure()
            if attempt >= retry.max_attempts:
                logger.warning("%s: исчерпаны попытки (%d): %s", label, attempt, exc)
                raise
            backoff = retry.min_backoff_seconds * (2 ** (attempt - 1))
            delay = min(retry.max_backoff_seconds, backoff)
            delay += random.uniform(0, retry.jitter_seconds)
            logger.warning(
                "%s: попытка %d/%d не удалась (%s), retry через %.1f с",
                label,
                attempt,
                retry.max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
