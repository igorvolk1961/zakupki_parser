"""Контроль формата кодов ОКПД2 (нормализация и валидация)."""

from __future__ import annotations

import pytest

from zakupki_parser.okpd import normalize_okpd_code, normalize_okpd_codes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("62.02", "62.02"),
        ("62", "62"),
        ("62.02.20.110", "62.02.20.110"),
        ("62-02", "62.02"),
        ("62 02 2", "62.02.2"),
        ("  6202  ", "6202"),
        ("6202", "6202"),
    ],
)
def test_normalize_okpd_valid(raw: str, expected: str) -> None:
    assert normalize_okpd_code(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "abc",
        "62.a2",
        "6",
        "1234567890",  # 10 цифр
        "62.02..",
        "..62",
        "62..02",
    ],
)
def test_normalize_okpd_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_okpd_code(raw)


def test_normalize_okpd_codes_dedup() -> None:
    assert normalize_okpd_codes(["62.02", " 62.02 ", "62", "62.02"]) == ["62.02", "62"]


def test_normalize_okpd_codes_none_and_empty() -> None:
    assert normalize_okpd_codes(None) == []
    assert normalize_okpd_codes([]) == []
    assert normalize_okpd_codes([None]) == []
