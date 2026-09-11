"""Тесты проверки принадлежности кода ОКПД2 списку конфигурируемых префиксов.

Используется для скоупинга фоновой индексации по ОКПД2 (индексный профиль,
роутинг «горячего» пересбора) — сравнение по иерархии нормализованного кода,
а не подстрокой сырой строки (см. okpd_code_covered_by_prefixes docstring).
"""

from __future__ import annotations

import pytest

from zakupki_parser.okpd import (
    any_okpd_code_covered_by_prefixes,
    normalize_okpd2_field,
    okpd_code_covered_by_prefixes,
)


@pytest.mark.parametrize(
    ("code", "prefixes", "expected"),
    [
        ("62.01.11", ["62", "38"], True),
        ("62", ["62"], True),
        ("38.11.10", ["38"], True),
        ("71.20", ["62", "38"], False),
        # Раздел 26 не должен ложно совпасть с префиксом «62», даже если цифры
        # «62» встречаются где-то внутри кода — сравнение по префиксу иерархии.
        ("26.62.11", ["62"], False),
        ("71.38.10", ["38"], False),
        # Обратная сторона: длинный код внутри своего же раздела совпадает.
        ("62.02.20.110", ["62.02"], True),
        ("62.03.20.110", ["62.02"], False),
    ],
)
def test_okpd_code_covered_by_prefixes(code: str, prefixes: list[str], expected: bool) -> None:
    assert okpd_code_covered_by_prefixes(code, prefixes) is expected


def test_okpd_code_covered_by_prefixes_invalid_inputs_are_safe() -> None:
    assert okpd_code_covered_by_prefixes("не-код", ["62"]) is False
    assert okpd_code_covered_by_prefixes("62.01", ["не-код"]) is False
    assert okpd_code_covered_by_prefixes("62.01", []) is False


@pytest.mark.parametrize(
    ("raw_codes", "prefixes", "expected"),
    [
        (None, ["62"], False),
        ("", ["62"], False),
        ("71.20", ["62", "38"], False),
        # Актуальный код — не первый в списке (площадка отдаёт несколько кодов
        # для одной закупки через запятую).
        ("71.20, 62.01.11", ["62", "38"], True),
        ("71.20; 38.11", ["62", "38"], True),
        ("26.62.11, 71.20", ["62"], False),
    ],
)
def test_any_okpd_code_covered_by_prefixes(
    raw_codes: str | None, prefixes: list[str], expected: bool
) -> None:
    assert any_okpd_code_covered_by_prefixes(raw_codes, prefixes) is expected


@pytest.mark.parametrize(
    ("raw_codes", "expected"),
    [
        (None, None),
        ("", None),
        ("62.01.11", "62.01.11"),
        ("71.20, 62.01.11", "71.20, 62.01.11"),
        ("не-код", None),
        ("71.20, не-код, 62.01.11", "71.20, 62.01.11"),
        ("62.01, 62.01", "62.01"),
    ],
)
def test_normalize_okpd2_field(raw_codes: str | None, expected: str | None) -> None:
    assert normalize_okpd2_field(raw_codes) == expected
