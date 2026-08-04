"""Unit-тесты классификации ошибок БД (для circuit breaker и retry)."""

from __future__ import annotations

from asyncpg.exceptions import (
    ConnectionDoesNotExistError,
    InterfaceError,
    StringDataRightTruncationError,
)

from zakupki_parser.storage.db_errors import is_data_db_error, is_transient_db_error


def test_transient_connection_error() -> None:
    assert is_transient_db_error(ConnectionDoesNotExistError("no conn")) is True


def test_transient_interface_error() -> None:
    assert is_transient_db_error(InterfaceError("closed")) is True


def test_data_error_is_not_transient() -> None:
    err = StringDataRightTruncationError("value too long")
    assert is_data_db_error(err) is True
    assert is_transient_db_error(err) is False


def test_transient_is_not_data_error() -> None:
    assert is_data_db_error(ConnectionDoesNotExistError("no conn")) is False
