"""Unit-тесты ретраев с экспоненциальным бэкоффом и circuit breaker."""

from __future__ import annotations

import pytest

from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError, CircuitState
from zakupki_parser.config.models import RetryConfig
from zakupki_parser.retry import run_with_retry


def _cfg(max_attempts: int = 3) -> RetryConfig:
    # min_backoff=0 и jitter=0 — чтобы тесты не спали
    return RetryConfig(max_attempts=max_attempts, min_backoff_seconds=0, jitter_seconds=0)


@pytest.mark.asyncio
async def test_success_first_try() -> None:
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await run_with_retry(op, retry=_cfg()) == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_then_succeeds() -> None:
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("boom")
        return "ok"

    assert await run_with_retry(op, retry=_cfg(max_attempts=5)) == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_exhausts_raises() -> None:
    async def op() -> str:
        raise TimeoutError("timeout")

    with pytest.raises(TimeoutError):
        await run_with_retry(op, retry=_cfg(max_attempts=2))


@pytest.mark.asyncio
async def test_success_resets_circuit() -> None:
    cb = CircuitBreaker("t", failure_threshold=2, reset_timeout_seconds=60)

    async def op() -> int:
        return 1

    await run_with_retry(op, retry=_cfg(), circuit=cb)
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_opens_and_aborts() -> None:
    cb = CircuitBreaker("t", failure_threshold=2, reset_timeout_seconds=60)

    async def op() -> str:
        raise RuntimeError("x")

    with pytest.raises(CircuitOpenError):
        await run_with_retry(op, retry=_cfg(max_attempts=5), circuit=cb)
    assert cb.state == CircuitState.OPEN
