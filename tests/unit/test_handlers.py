"""Unit-тесты обработчиков значений."""

from __future__ import annotations

from zakupki_parser.parser.handlers import (
    apply_handler,
    handler_date_iso,
    handler_deadline,
    handler_int,
    handler_law,
    handler_money,
    handler_pub_date,
    handler_regex,
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


def test_pub_date() -> None:
    dt = handler_pub_date("с 06.08.2026 до 06.08.2026 15:00 (МСК)")
    assert dt is not None and dt.isoformat().startswith("2026-08-06T00:00:00")
    assert handler_pub_date("garbage") is None


def test_deadline() -> None:
    dt = handler_deadline("с 06.08.2026 до 06.08.2026 15:00 (МСК)")
    assert dt is not None and dt.isoformat().startswith("2026-08-06T15:00:00")
    assert handler_deadline("garbage") is None


def test_law() -> None:
    assert handler_law("г Сургут 44-ФЗ с 06.08.2026") == "44-ФЗ"
    assert handler_law("г Москва 223-ФЗ") == "223-ФЗ"
    assert handler_law("без закона") is None


def test_regex() -> None:
    assert handler_regex("с 06.08.2026 до 06.08.2026", r"с (\d{2}\.\d{2}\.\d{4})") == "06.08.2026"
    assert handler_regex("abc", None) is None
