"""Тесты сериализации профиля в единый JSON-файл с подобъектом компетенций.

Компетенции — всегда канонический JSON схемы Profile (BR-07): legacy-режимы
raw/markdown не поддерживаются. Невалидные/не-JSON значения отклоняются.
"""

from __future__ import annotations

import json

import pytest

from zakupki_parser.storage.competencies import CompetenciesError
from zakupki_parser.storage.profile_json import (
    SCHEMA,
    VERSION,
    parse_profile_json,
    serialize_profile_json,
)


def test_serialize_structured_competencies() -> None:
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
    assert payload["schema"] == SCHEMA
    assert payload["version"] == VERSION
    assert payload["profile"]["name"] == "x"
    assert payload["competencies"]["positioning"] == "Внедряем ИИ"
    assert payload["competencies"]["competencies"][0]["area"] == "Аудит"


def test_serialize_invalid_competencies_skipped() -> None:
    """Легаси/свободный текст компетенций -> пустой подобъект (не искажаем схему)."""
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
    payload = json.loads(serialize_profile_json(profile))
    assert payload["schema"] == SCHEMA
    assert payload["competencies"] == {}


def test_parse_roundtrip_json() -> None:
    """Экспорт -> импорт сохраняет компетенции и слова без потерь."""
    structured = {
        "positioning": "Внедряем ИИ",
        "breadth": "broad",
        "competencies": [{"area": "Аудит", "description": "обследование", "examples": ["кейс1"]}],
        "exclusions": [],
        "scoring_policy": {"uncovered_penalty": 1.5, "ambiguous_range": [4.0, 6.0]},
    }
    profile = {
        "name": "bbk-it",
        "enabled": True,
        "is_active": True,
        "competencies": json.dumps(structured),
        "keywords": ["ИИ", "автоматизация"],
        "exclusion_words": ["ремонт"],
        "okpd_codes": ["62"],
        "nmck_min": 100000,
        "nmck_max": 5000000,
        "min_fit_threshold": 1.5,
        "target_etp": [],
        "target_laws": [],
        "target_regions": ["Москва", "Московская область"],
        "max_region_distance_km": 100.0,
        "questions": [{"id": "q1", "text": "Нужна лицензия?"}],
    }
    seed = parse_profile_json(serialize_profile_json(profile))
    assert seed["name"] == "bbk-it"
    # Канонический JSON проходит через модель Profile: добавляются дефолтные поля
    # (name и т.п.), компетенции сохраняются без потерь.
    from zakupki_parser.storage.competencies import normalize_competencies

    assert json.loads(seed["competencies"]) == json.loads(
        normalize_competencies(json.dumps(structured, ensure_ascii=False))
    )
    assert seed["keywords"] == ["ИИ", "автоматизация"]
    assert seed["exclusion_words"] == ["ремонт"]
    assert seed["okpd_codes"] == ["62"]
    assert seed["nmck_min"] == 100000
    assert seed["questions"] == [{"id": "q1", "text": "Нужна лицензия?"}]
    assert seed["target_etp"] == []
    assert seed["target_laws"] == []
    assert seed["target_regions"] == ["Москва", "Московская область"]
    assert seed["max_region_distance_km"] == 100.0


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
    from zakupki_parser.storage.competencies import normalize_competencies

    assert json.loads(seed["competencies"]) == json.loads(
        normalize_competencies(json.dumps(structured, ensure_ascii=False))
    )


def test_parse_missing_name_defaults() -> None:
    seed = parse_profile_json(json.dumps({"competencies": {"positioning": "П"}}))
    assert seed["name"] == "default"


def test_parse_rejects_non_json_competencies() -> None:
    """Свободный текст компетенций отклоняется (легаси нет): только JSON-схема."""
    with pytest.raises(CompetenciesError):
        parse_profile_json(json.dumps({"competencies": "gibberish"}))


def test_parse_empty_competencies_yields_empty_profile_json() -> None:
    """Пустые компетенции -> канонический JSON пустого профиля (проверка пустоты выше)."""
    seed = parse_profile_json(json.dumps({"profile": {"name": "x"}, "competencies": {}}))
    profile = json.loads(seed["competencies"])
    assert profile["positioning"] == ""
    assert profile["competencies"] == []


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
                    "competencies": {"positioning": "Внедряем ИИ"},
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


def test_parse_rejects_invalid_competencies_schema() -> None:
    """JSON не схемы Profile (например, со строковым competencies) отклоняется."""
    payload = json.dumps({"profile": {"name": "x"}, "competencies": {"competencies": "not-list"}})
    with pytest.raises(CompetenciesError):
        parse_profile_json(payload)


def test_parse_target_regions_defaults_to_empty() -> None:
    """Целевые регионы не заданы — пустой список (как target_laws)."""
    seed = parse_profile_json(
        json.dumps({"profile": {"name": "x"}, "competencies": {"positioning": "П"}})
    )
    assert seed["target_regions"] == []
    assert seed["max_region_distance_km"] is None
    # Отсутствующее поле сериализуется пустым списком.
    payload = json.loads(serialize_profile_json({"name": "x", "competencies": "{}"}))
    assert payload["profile"]["target_regions"] == []
    assert payload["profile"]["max_region_distance_km"] is None


def test_parse_target_regions_distance_coerced() -> None:
    """max_region_distance_km приводится к float (как другие числовые поля)."""
    seed = parse_profile_json(
        json.dumps(
            {
                "profile": {
                    "name": "x",
                    "target_regions": ["Московск* обл*"],
                    "max_region_distance_km": "120",
                },
                "competencies": {"positioning": "П"},
            }
        )
    )
    assert seed["target_regions"] == ["Московск* обл*"]
    assert seed["max_region_distance_km"] == 120.0


def test_parse_target_regions_coerces_list() -> None:
    seed = parse_profile_json(
        json.dumps(
            {
                "profile": {"name": "x", "target_regions": ["Москва", "Санкт-Петербург"]},
                "competencies": {"positioning": "П"},
            }
        )
    )
    assert seed["target_regions"] == ["Москва", "Санкт-Петербург"]


def test_parse_rejects_string_target_regions() -> None:
    """Строка в списковом поле не разбивается на символы (как okpd_codes)."""
    payload = json.dumps({"profile": {"name": "x", "target_regions": "Москва"}})
    with pytest.raises(ValueError):
        parse_profile_json(payload)
