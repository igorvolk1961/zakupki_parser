"""Классификация ошибок БД (для circuit breaker и retry).

Транзиентные ошибки (недоступность/сеть) учитываются circuit breaker'ом и
повторяются; ошибки данных/схемы — нет.
"""

from __future__ import annotations

import asyncpg
from sqlalchemy.exc import DBAPIError


def unwrap_db_error(exc: BaseException) -> BaseException:
    """Распаковывает SQLAlchemy DBAPIError до исходного (asyncpg) исключения."""
    while isinstance(exc, DBAPIError) and exc.orig is not None:
        exc = exc.orig
    return exc


def is_transient_db_error(exc: BaseException) -> bool:
    """Транзиентная ошибка (недоступность БД/сети) — учитывается circuit breaker'ом."""
    exc = unwrap_db_error(exc)
    return isinstance(
        exc,
        (
            asyncpg.PostgresConnectionError,
            asyncpg.InterfaceError,
            OSError,
            TimeoutError,
        ),
    )


def is_data_db_error(exc: BaseException) -> bool:
    """Ошибка данных/схемы (не транзиентная) — НЕ учитывается circuit breaker'ом."""
    exc = unwrap_db_error(exc)
    return isinstance(exc, asyncpg.DataError)
