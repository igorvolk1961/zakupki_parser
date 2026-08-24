"""Тесты извлечения описания закупки."""

from __future__ import annotations

from scoring_service.pipeline.description import (
    extend_description_from_tz,
    extract_description,
    is_truncated_description,
)


def test_extract_uses_subject() -> None:
    record = {"subject": "Разработка ПО", "nmck": 100}
    desc = extract_description(record)
    assert "Разработка ПО" in desc
    assert "nmck: 100" in desc


def test_extract_uses_detail_json() -> None:
    record = {
        "subject": "Аудит ИТ",
        "detail_json": {"okpd2_name": "Программное обеспечение", "law": "44-ФЗ"},
    }
    desc = extract_description(record)
    assert "Аудит ИТ" in desc
    assert "okpd2_name: Программное обеспечение" in desc
    assert "law: 44-ФЗ" in desc


def test_extract_includes_okpd2_codes() -> None:
    record = {
        "subject": "Сопровождение системы автоматизации",
        "okpd2_codes": "62.02.30.000",
        "kpgz_codes": "62.20",
    }
    desc = extract_description(record)
    assert "okpd2_codes: 62.02.30.000" in desc
    assert "kpgz_codes: 62.20" in desc


def test_extract_empty() -> None:
    assert "(описание отсутствует)" in extract_description({})


def test_is_truncated_true_for_ellipsis() -> None:
    assert is_truncated_description("Разработка ПО и внедрение системы...")
    assert is_truncated_description("Разработка ПО и внедрение системы…")
    assert is_truncated_description("Разработка ПО и внедрение системы..")


def test_is_truncated_false_for_full() -> None:
    assert not is_truncated_description("Разработка ПО и внедрение системы")
    assert not is_truncated_description("")
    assert not is_truncated_description("Текст с точкой в середине.")


def test_extend_description_finds_header_line() -> None:
    tz = (
        "Разработка и внедрение системы автоматизации документооборота предприятия\n"
        "Общие положения...\n"
    )
    out = extend_description_from_tz(
        "Разработка и внедрение системы автоматизации документооборота", tz
    )
    assert out == "Разработка и внедрение системы автоматизации документооборота предприятия"


def test_extend_description_finds_by_prefix() -> None:
    tz = "Внедрение ПО для оптимизации потоков обработки информации в организации\nдалее..."
    out = extend_description_from_tz("Внедрение ПО для оптимизации потоков", tz)
    assert out == "Внедрение ПО для оптимизации потоков обработки информации в организации"


def test_extend_description_finds_line_containing_prefix() -> None:
    """Префикс не обязан стоять в начале строки: markdown-заголовок «# …» тоже подходит."""
    tz = "# Разработка и внедрение системы автоматизации документооборота предприятия\n"
    out = extend_description_from_tz(
        "Разработка и внедрение системы автоматизации документооборота", tz
    )
    assert out == "# Разработка и внедрение системы автоматизации документооборота предприятия"


def test_extend_description_matches_prefix_inside_line() -> None:
    """Строка ТЗ, лишь содержащая префикс subject, возвращается целиком."""
    tz = "Оказание услуг: Внедрение ПО для оптимизации потоков обработки информации в организации\n"
    out = extend_description_from_tz("Внедрение ПО для оптимизации потоков", tz)
    assert (
        out
        == "Оказание услуг: Внедрение ПО для оптимизации потоков обработки информации в организации"
    )


def test_extend_description_not_found_returns_none() -> None:
    assert extend_description_from_tz("Совершенно другое описание", "текст ТЗ") is None
    assert extend_description_from_tz("", "текст ТЗ") is None
    assert extend_description_from_tz("описание", "") is None
