"""Тесты извлечения описания закупки."""

from __future__ import annotations

from scoring_service.pipeline.description import extract_description


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
