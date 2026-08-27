"""Тесты сериализации профиля в единый JSON-файл с подобъектом компетенций."""

from __future__ import annotations

import json

import pytest

from zakupki_parser.storage.profile_json import (
    SCHEMA,
    VERSION,
    parse_profile_json,
    serialize_profile_json,
)


def test_serialize_raw_competencies_block() -> None:
    """Legacy-текст компетенций — сырой подобъект, а не ссылка на файл."""
    profile = {
        "name": "bbk-it",
        "enabled": True,
        "is_active": True,
        "competencies": "Поставщик — BBK IT.\nКомпетенции: ИИ.",
        "keywords": ["ИИ"],
        "exclusion_words": ["ремонт"],
        "okpd_codes": ["62"],
        "questions": [],
    }
    content = serialize_profile_json(profile)
    payload = json.loads(content)
    assert payload["schema"] == SCHEMA
    assert payload["version"] == VERSION
    assert payload["profile"]["name"] == "bbk-it"
    assert payload["competencies"]["mode"] == "raw"
    assert "ИИ" in payload["competencies"]["text"]


def test_serialize_structured_competencies_roundtrip() -> None:
    """Структурированные компетенции сохраняются в формате scoring Profile."""
    structured = {
        "positioning": "Внедряем ИИ",
        "breadth": "narrow",
        "competencies": [{"area": "Аудит", "description": "обследование", "examples": ["кейс1"]}],
        "exclusions": ["поставка"],
        "scoring_policy": {"uncovered_penalty": 3.0, "ambiguous_range": [5.0, 7.0]},
    }
    profile = {"name": "x", "competencies": json.dumps(structured)}
    payload = json.loads(serialize_profile_json(profile))
    assert payload["competencies"]["mode"] == "structured"
    assert payload["competencies"]["positioning"] == "Внедряем ИИ"


def test_parse_roundtrip_json() -> None:
    """Экспорт -> импорт сохраняет компетенции и слова без потерь."""
    profile = {
        "name": "bbk-it",
        "enabled": True,
        "is_active": True,
        "competencies": "Поставщик — BBK IT.\nКомпетенции: ИИ.",
        "keywords": ["ИИ", "автоматизация"],
        "exclusion_words": ["ремонт"],
        "okpd_codes": ["62"],
        "nmck_min": 100000,
        "nmck_max": 5000000,
        "min_fit_threshold": 1.5,
        "target_etp": [],
        "target_laws": [],
        "questions": [{"id": "q1", "text": "Нужна лицензия?"}],
    }
    seed = parse_profile_json(serialize_profile_json(profile))
    assert seed["name"] == "bbk-it"
    assert seed["competencies"] == profile["competencies"]
    assert seed["keywords"] == ["ИИ", "автоматизация"]
    assert seed["exclusion_words"] == ["ремонт"]
    assert seed["okpd_codes"] == ["62"]
    assert seed["nmck_min"] == 100000
    assert seed["questions"] == [{"id": "q1", "text": "Нужна лицензия?"}]


def test_parse_structured_competencies_stored_compact() -> None:
    """Структурированный подобъект импортируется компактной JSON-строкой для БД."""
    structured = {
        "positioning": "Внедряем ИИ",
        "breadth": "broad",
        "competencies": [],
        "exclusions": [],
        "scoring_policy": {"uncovered_penalty": 1.5, "ambiguous_range": [4.0, 6.0]},
    }
    content = json.dumps({"profile": {"name": "x"}, "competencies": structured})
    seed = parse_profile_json(content)
    assert json.loads(seed["competencies"]) == structured


def test_parse_missing_name_defaults() -> None:
    seed = parse_profile_json(json.dumps({"competencies": "gibberish"}))
    assert seed["name"] == "default"
    assert seed["competencies"] == "gibberish"


def test_parse_empty_competencies() -> None:
    seed = parse_profile_json(
        json.dumps({"profile": {"name": "x"}, "competencies": {"mode": "empty"}})
    )
    assert seed["competencies"] == ""


def test_parse_rejects_non_numeric_nmck() -> None:
    """Не-числовое значение НМЦК не пишется в Float-колонку (иначе 500)."""
    payload = json.dumps({"profile": {"name": "x", "nmck_min": "abc"}})
    with pytest.raises(ValueError):
        parse_profile_json(payload)


def test_parse_rejects_string_okpd_codes() -> None:
    """Строка в списковом поле не разбивается на символы (``list("62")``)."""
    payload = json.dumps({"profile": {"name": "x", "okpd_codes": "62"}})
    with pytest.raises(ValueError):
        parse_profile_json(payload)


def test_parse_coerces_types() -> None:
    """Числовые строки и списки приводятся к типам колонок."""
    seed = parse_profile_json(
        json.dumps(
            {
                "profile": {
                    "name": "x",
                    "nmck_min": 100000,
                    "nmck_max": "5000000",
                    "enabled": True,
                    "is_active": False,
                    "okpd_codes": ["62", "62.01"],
                    "target_etp": ["zakupki_mos"],
                }
            }
        )
    )
    assert seed["nmck_min"] == 100000.0
    assert seed["nmck_max"] == 5000000.0
    assert seed["enabled"] is True
    assert seed["is_active"] is False
    assert seed["okpd_codes"] == ["62", "62.01"]
    assert seed["target_etp"] == ["zakupki_mos"]
