"""Unit-тесты классификации метаданных файлов (без скачивания)."""

from __future__ import annotations

from zakupki_parser.parser.files import (
    TECHNICAL_SPEC_KEYWORDS,
    _matches_keywords,
    split_technical_spec,
)


def test_default_keywords_include_ts() -> None:
    assert "техническое задание" in TECHNICAL_SPEC_KEYWORDS


def test_matches_keywords_case_insensitive() -> None:
    assert _matches_keywords("Техническое задание.pdf") is True
    assert _matches_keywords("техническое задание.pdf") is True
    assert _matches_keywords("Приложение 1.docx") is False
    assert _matches_keywords(None) is False
    assert _matches_keywords("") is False


def test_split_technical_spec() -> None:
    files = [
        {"name": "Техническое задание.pdf", "url": "https://etp/1"},
        {"name": "Приложение 1.docx", "url": "https://etp/2"},
        {"name": "проект договора.docx", "url": "https://etp/3"},
    ]
    ts, others = split_technical_spec(files)
    assert ts == [{"name": "Техническое задание.pdf", "url": "https://etp/1"}]
    assert len(others) == 2
    assert all(f["name"] != "Техническое задание.pdf" for f in others)
