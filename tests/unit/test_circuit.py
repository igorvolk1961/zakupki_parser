"""Unit-тесты circuit breaker."""

from __future__ import annotations

from zakupki_parser.circuit import CircuitBreaker, CircuitState


def test_initial_closed() -> None:
    cb = CircuitBreaker("t", failure_threshold=3, reset_timeout_seconds=60)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_opens_after_threshold() -> None:
    cb = CircuitBreaker("t", failure_threshold=3, reset_timeout_seconds=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.allow_request() is True
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_half_open_success_resets() -> None:
    cb = CircuitBreaker("t", failure_threshold=1, reset_timeout_seconds=-1)
    # reset_timeout зажат до >=1, поэтому ждём прохода таймера
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False
    # принудительно открываем пробный доступ вручную
    cb._state = CircuitState.OPEN  # noqa: SLF001
    cb._opened_at = 0  # noqa: SLF001
    assert cb.allow_request() is True  # HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED  # type: ignore[comparison-overlap]


def test_half_open_failure_reopens() -> None:
    cb = CircuitBreaker("t", failure_threshold=1, reset_timeout_seconds=60)
    cb.record_failure()
    cb._state = CircuitState.OPEN  # noqa: SLF001
    cb._opened_at = 0  # noqa: SLF001
    assert cb.allow_request() is True  # -> HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
