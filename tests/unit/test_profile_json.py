"""Тесты сериализации профиля в единый JSON-файл с подобъектом компетенций.

Компетенции — всегда канонический JSON схемы Profile (BR-07): legacy-режимы
raw/markdown не поддерживаются. Невалидные/не-JSON значения отклоняются.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from zakupki_parser.storage.competencies import CompetenciesError
from zakupki_parser.storage.profile_json import (
    SCHEMA,
    VERSION,
    parse_profile_json,
    resolve_profile_fact_refs,
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
        "search_in_documents": True,
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
    assert seed["search_in_documents"] is True


def test_parse_search_in_documents_defaults_to_false() -> None:
    seed = parse_profile_json(serialize_profile_json({"name": "x", "competencies": "{}"}))
    assert seed["search_in_documents"] is False


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


def test_serialize_includes_licenses_experience_portable() -> None:
    """Факты BR-03 сериализуются переносимыми ссылками (name/code) вместо id."""
    profile = {
        "name": "x",
        "competencies": "{}",
        "licenses": [
            {
                "license_type_id": 3,
                "license_type": {"id": 3, "name": "Лицензия на отходы"},
                "number": "077-123",
                "authority": "Росприроднадзор",
                "issue_date": date(2020, 1, 1),
                "expiry_date": None,
                "notes": "бессрочная",
            }
        ],
        "experience": [
            {
                "confirmation_type_id": 1,
                "confirmation_type": {"id": 1, "code": "platform", "name": "Через площадку"},
                "title": "Услуги по утилизации отходов",
                "customer_name": "АО «Мосводоканал»",
                "contract_number": "123-К",
                "start_date": date(2023, 1, 1),
                "end_date": date(2024, 1, 1),
                "amount": 4370184.0,
                "import_independent": None,
                "notes": None,
            }
        ],
    }
    payload = json.loads(serialize_profile_json(profile))
    lic = payload["profile"]["licenses"][0]
    assert lic["license_type_name"] == "Лицензия на отходы"
    assert lic["license_type_id"] == 3
    assert lic["issue_date"] == "2020-01-01"
    assert lic["expiry_date"] is None
    exp = payload["profile"]["experience"][0]
    assert exp["confirmation_type_code"] == "platform"
    assert exp["confirmation_type_id"] == 1
    assert exp["start_date"] == "2023-01-01"
    assert exp["import_independent"] is None


def test_parse_licenses_experience_flat_form() -> None:
    """Плоская форма (licenses/experience в корне profile) читается в seed."""
    content = json.dumps(
        {
            "profile": {
                "name": "Экопаттерн",
                "licenses": [
                    {
                        "license_type_name": "Лицензия на отходы",
                        "license_type_id": 3,
                        "number": "077-123",
                    }
                ],
                "experience": [
                    {
                        "confirmation_type_code": "platform",
                        "confirmation_type_id": 1,
                        "title": "Утилизация отходов",
                    }
                ],
            },
            "competencies": {"positioning": "Утилизация отходов"},
        }
    )
    seed = parse_profile_json(content)
    assert seed["licenses"] == [
        {
            "license_type_id": 3,
            "license_type_name": "Лицензия на отходы",
            "number": "077-123",
            "authority": None,
            "issue_date": None,
            "expiry_date": None,
            "notes": None,
        }
    ]
    assert seed["experience"][0]["confirmation_type_code"] == "platform"


def test_parse_omits_facts_when_absent() -> None:
    """Ключи licenlsé/experience отсутствуют — импорт их не трогает (не стирает БД)."""
    seed = parse_profile_json(json.dumps({"profile": {"name": "x"}, "competencies": {}}))
    assert "licenses" not in seed
    assert "experience" not in seed


def test_parse_rejects_non_list_licenses() -> None:
    """licenses не списком — ValueError (как okpd_codes строкой)."""
    payload = json.dumps({"profile": {"name": "x", "licenses": "077-123"}})
    with pytest.raises(ValueError):
        parse_profile_json(payload)


def test_resolve_profile_fact_refs_resolves_codes() -> None:
    """name/code резолвятся в id справочников; ссылки отбрасываются."""
    seed = parse_profile_json(
        json.dumps(
            {
                "profile": {
                    "name": "x",
                    "licenses": [{"license_type_name": "Лицензия на отходы", "number": "077"}],
                    "experience": [
                        {"confirmation_type_code": "platform", "title": "Опыт", "amount": 1.0}
                    ],
                },
                "competencies": {"positioning": "П"},
            }
        )
    )
    resolved = resolve_profile_fact_refs(
        seed,
        license_name_to_id={"Лицензия на отходы": 7},
        confirmation_code_to_id={"platform": 2},
    )
    assert resolved["licenses"][0]["license_type_id"] == 7
    assert "license_type_name" not in resolved["licenses"][0]
    assert resolved["experience"][0]["confirmation_type_id"] == 2
    assert "confirmation_type_code" not in resolved["experience"][0]


def test_resolve_profile_fact_refs_unknown_reference() -> None:
    """Неизвестная ссылка (name/code) — ValueError на этапе резолва."""
    seed = {
        "name": "x",
        "licenses": [{"license_type_name": "Нет такого вида", "number": "077"}],
    }
    with pytest.raises(ValueError):
        resolve_profile_fact_refs(seed, license_name_to_id={}, confirmation_code_to_id={})


def test_resolve_profile_fact_refs_coerces_column_types() -> None:
    """ISO-даты приводятся к date, сумма — к float, флаг — к bool (иначе asyncpg DataError)."""
    seed = parse_profile_json(
        json.dumps(
            {
                "profile": {
                    "name": "x",
                    "licenses": [
                        {
                            "license_type_name": "Лицензия на отходы",
                            "number": "077",
                            "issue_date": "2025-02-14",
                            "expiry_date": None,
                        }
                    ],
                    "experience": [
                        {
                            "confirmation_type_code": "platform",
                            "title": "Опыт",
                            "start_date": "2025-03-01",
                            "end_date": "2025-12-31",
                            "amount": "4370184.0",
                            "import_independent": True,
                        }
                    ],
                },
                "competencies": {"positioning": "П"},
            }
        )
    )
    resolved = resolve_profile_fact_refs(
        seed,
        license_name_to_id={"Лицензия на отходы": 7},
        confirmation_code_to_id={"platform": 2},
    )
    lic = resolved["licenses"][0]
    exp = resolved["experience"][0]
    assert lic["issue_date"] == date(2025, 2, 14)
    assert lic["issue_date"].__class__ is date
    assert lic["expiry_date"] is None
    assert exp["start_date"] == date(2025, 3, 1)
    assert exp["end_date"] == date(2025, 12, 31)
    assert exp["amount"] == 4370184.0
    assert exp["import_independent"] is True
