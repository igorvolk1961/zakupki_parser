"""Unit-тесты обработчиков значений."""

from __future__ import annotations

from zakupki_parser.parser.handlers import (
    apply_handler,
    handler_date_iso,
    handler_int,
    handler_money,
    handler_strip,
)


def test_strip() -> None:
    assert handler_strip("  hi  ") == "hi"
    assert handler_strip(None) == ""


def test_money() -> None:
    assert handler_money("3 250,00 ₽") == 3250.0
    assert handler_money("186 000,00 ₽") == 186000.0
    assert handler_money(None) is None


def test_int() -> None:
    assert handler_int("42") == 42
    assert handler_int("abc") is None


def test_date_iso() -> None:
    assert handler_date_iso("06.08.2026") == "2026-08-06T00:00:00"
    assert handler_date_iso("05.08.2026 10:00") == "2026-08-05T10:00:00"
    assert handler_date_iso("garbage") is None


def test_apply_handler_none() -> None:
    assert apply_handler(None, "value") == "value"
    assert apply_handler("lower", "AbC") == "abc"
