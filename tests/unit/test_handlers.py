"""Unit-тесты обработчиков значений."""

from __future__ import annotations

from datetime import datetime

from zakupki_parser.parser.handlers import (
    apply_handler,
    handler_date_iso,
    handler_datetime,
    handler_deadline,
    handler_int,
    handler_law,
    handler_money,
    handler_pub_date,
    handler_regex,
    handler_regex_datetime,
    handler_ru_date,
    handler_security,
    handler_security_unit,
    handler_strip,
)


def test_strip() -> None:
    assert handler_strip("  hi  ") == "hi"
    assert handler_strip(None) == ""


def test_money() -> None:
    assert handler_money("3 250,00 ₽") == 3250.0
    assert handler_money("186 000,00 ₽") == 186000.0
    assert handler_money(None) is None


def test_security() -> None:
    assert handler_security("10 %") == 10.0
    assert handler_security("3 600 239,70 Российский рубль (12,5 %)") == 3600239.7
    assert handler_security("10\u00a0%") == 10.0
    assert handler_security(None) is None


def test_security_unit() -> None:
    assert handler_security_unit("10 %") == "%"
    assert handler_security_unit("10\u00a0%") == "%"
    assert handler_security_unit("3 600 239,70 Российский рубль (12,5 %)") == "руб."
    assert handler_security_unit(None) is None


def test_int() -> None:
    assert handler_int("42") == 42
    assert handler_int("abc") is None


def test_date_iso() -> None:
    assert handler_date_iso("06.08.2026") == "2026-08-06T00:00:00"
    assert handler_date_iso("05.08.2026 10:00") == "2026-08-05T10:00:00"
    assert handler_date_iso("garbage") is None


def _iso(value: datetime | None) -> str:
    assert isinstance(value, datetime)
    return value.isoformat()


def test_datetime() -> None:
    assert _iso(handler_datetime("12.08.2026 01:12")) == "2026-08-12T01:12:00+03:00"
    assert _iso(handler_datetime("18.08.2026 10:00")) == "2026-08-18T10:00:00+03:00"
    assert _iso(handler_datetime("06.08.2026")) == "2026-08-06T00:00:00+03:00"
    assert handler_datetime("garbage") is None
    assert handler_datetime(None) is None


def test_regex_datetime() -> None:
    assert (
        _iso(
            handler_regex_datetime(
                "Опубликована 12.08.2026 00:55", r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})"
            )
        )
        == "2026-08-12T00:55:00+03:00"
    )
    assert (
        _iso(
            handler_regex_datetime(
                "12.08.2026 00:55 - 20.08.2026 05:56 (MSK+00:00)",
                r"- (\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})",
            )
        )
        == "2026-08-20T05:56:00+03:00"
    )
    assert handler_regex_datetime("garbage", r"(\d)") is None
    assert handler_regex_datetime(None, r"(\d)") is None


def test_ru_date() -> None:
    assert _iso(handler_ru_date("14 августа 2026, 18:00 МСК")) == "2026-08-14T18:00:00+03:00"
    assert _iso(handler_ru_date("11 августа 2026, 23:25")) == "2026-08-11T23:25:00+03:00"
    assert _iso(handler_ru_date("01 января 2026")) == "2026-01-01T00:00:00+03:00"
    assert handler_ru_date("не дата") is None
    assert handler_ru_date(None) is None


def test_apply_handler_none() -> None:
    assert apply_handler(None, "value") == "value"
    assert apply_handler("lower", "AbC") == "abc"


def test_apply_handler_passes_arg_to_regex_handlers() -> None:
    # apply_handler должен передавать handler_arg и в regex, и в regex_datetime
    # (ранее аргумент доходил только до "regex" — даты с regex_datetime падали в None).
    assert apply_handler("regex", "№ 1167043-1", r"№\s*(\d+(?:-\d+)?)") == "1167043-1"
    assert (
        _iso(
            apply_handler(
                "regex_datetime",
                "Опубликована 12.08.2026 00:55",
                r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})",
            )
        )
        == "2026-08-12T00:55:00+03:00"
    )


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


def test_law_from_full_block() -> None:
    # Текст всего нижнего блока карточки — расклад полей не важен
    block = "г Сургут 44-ФЗ с 06.08.2026 до 06.08.2026 15:00 (МСК)"
    assert handler_law(block) == "44-ФЗ"
    # B2B-карточка без федерального закона → None
    assert handler_law("ЕЭТП B2B с 06.08.2026 до 11.08.2026 15:30 (МСК)") is None
