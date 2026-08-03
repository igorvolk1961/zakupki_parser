"""Circuit Breaker для вежливой деградации при отказах БД и сайта.

Состояния:
- CLOSED — обычная работа; счётчик последовательных ошибок ведётся.
- OPEN   — отключено; вызовы сразу возвращают ошибку/пропуск до истечения таймера.
- HALF_OPEN — пробный вызов после таймера; при успехе сброс в CLOSED,
  при ошибке возврат в OPEN.
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Поднимается, когда circuit breaker в состоянии OPEN."""


class CircuitBreaker:
    """Простой счётчик-основанный circuit breaker (потокобезопасный)."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout_seconds: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout = max(1.0, reset_timeout_seconds)
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow_request(self) -> bool:
        """Возвращает True, если вызов разрешён, иначе False (OPEN)."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.reset_timeout:
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: разрешаем один пробный запрос
        return True

    def record_success(self) -> None:
        if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            logger.info("CB[%s]: успех, сброс в CLOSED", self.name)
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning("CB[%s]: пробный вызов провалился, OPEN", self.name)
            return
        if self._state == CircuitState.OPEN:
            return
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "CB[%s]: %d последовательных ошибок, OPEN на %.0f с",
                self.name,
                self.failure_threshold,
                self.reset_timeout,
            )

    def open_now(self) -> None:
        """Немедленно перевести в OPEN (например, при недоступности БД)."""
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
