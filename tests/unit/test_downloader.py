"""Unit-тесты скачивания файлов: фильтр по ключевым словам и валидация."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zakupki_parser.config.models import ServiceConfig
from zakupki_parser.downloader import _filename_from_disposition, _matches_keywords


def test_filename_from_disposition() -> None:
    assert _filename_from_disposition('attachment; filename="ТЗ.docx"') == "ТЗ.docx"
    assert (
        _filename_from_disposition("attachment; filename*=UTF-8''%D0%A2%D0%97.docx")
        == "%D0%A2%D0%97.docx"
    )
    assert _filename_from_disposition(None) is None


def test_matches_keywords() -> None:
    assert _matches_keywords("Техническое задание.pdf", ["техническое задание"]) is True
    assert _matches_keywords("техническое задание.pdf", ["Техническое задание"]) is True
    assert _matches_keywords("Приложение 1.docx", ["техническое задание"]) is False
    assert _matches_keywords(None, ["техническое задание"]) is False
    # несколько ключевых слов — достаточно одного
    assert _matches_keywords("ТЗ на поставку", ["техническое задание", "тз"]) is True


def test_ts_only_without_keywords_rejected() -> None:
    with pytest.raises(ValidationError):
        ServiceConfig(download_technical_spec_only=True, technical_spec_keywords=[])


def test_ts_only_with_keywords_ok() -> None:
    cfg = ServiceConfig(download_technical_spec_only=True)
    assert cfg.technical_spec_keywords == ["техническое задание"]


def test_ts_flag_off_with_empty_keywords_ok() -> None:
    cfg = ServiceConfig(download_technical_spec_only=False, technical_spec_keywords=[])
    assert cfg.technical_spec_keywords == []
