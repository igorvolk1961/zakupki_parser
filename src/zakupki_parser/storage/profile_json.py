"""Сериализация профиля в единый JSON-файл (экспорт/импорт) с подобъектом компетенций.

Формат — один файл без внешних ссылок: компетенции всегда внутри (подобъект),
поэтому файл можно выгрузить и повторно загрузить целиком. Для структурированного
редактора компетенций подобъект повторяет модель ``scoring_service.profile.Profile``
(``positioning``, ``breadth``, ``competencies[]``, ``exclusions``, ``scoring_policy``) —
именно эта структура при сохранении в БД (строка ``profile.competencies``) понимается
фронтендом (``parseComp``) и scoring-воркером (``profile_to_texts``). Legacy-текст
представляется как ``{"mode": "raw", "text": ...}``.
"""

from __future__ import annotations

import json
from typing import Any

from zakupki_parser.storage.competencies import normalize_competencies

SCHEMA = "zakupki-profile"
VERSION = 1


def _split_competencies(raw: str) -> dict[str, Any]:
    """Строка компетенций БД -> подобъект для экспорта.

    Компетенции всегда канонический JSON схемы ``Profile`` (BR-07): экспортируем
    как есть (валидированную модель), без legacy-режимов raw/empty.
    """
    if not raw or not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Легаси-значения не поддерживаются: экспорт не искажаем каноническую схему.
        return {}
    if isinstance(obj, dict):
        return obj
    return {}


def _join_competencies(block: Any) -> str:
    """Подобъект импорта -> строка компетенций для БД (канонический JSON схемы Profile).

    Принимает объект схемы ``Profile`` (dict): нормализуется через
    ``normalize_competencies``. Сырой текст/markdown не допускаются.
    """
    if block is None or block == "":
        # Пустой профиль: JSON пустого Profile (валидируется при сохранении).
        return normalize_competencies("")
    if isinstance(block, str):
        return normalize_competencies(block)
    if not isinstance(block, dict):
        raise ValueError("competencies должны быть JSON-объектом схемы Profile")
    return normalize_competencies(json.dumps(block, ensure_ascii=False))


def serialize_profile_json(profile: dict[str, Any]) -> str:
    """``ProfileOut.model_dump()`` -> JSON-текст файла, компетенции как подобъект."""
    block = _split_competencies(str(profile.get("competencies") or ""))
    payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "profile": {
            "name": profile.get("name"),
            "enabled": profile.get("enabled"),
            "is_active": profile.get("is_active"),
            "okpd_codes": profile.get("okpd_codes") or [],
            "nmck_min": profile.get("nmck_min"),
            "nmck_max": profile.get("nmck_max"),
            "min_fit_threshold": profile.get("min_fit_threshold"),
            "target_etp": profile.get("target_etp") or [],
            "target_laws": profile.get("target_laws") or [],
            "keywords": profile.get("keywords") or [],
            "exclusion_words": profile.get("exclusion_words") or [],
            "questions": profile.get("questions") or [],
        },
        "competencies": block,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _as_bool(value: Any) -> bool | None:
    """Булево/None, иначе ``ValueError`` (типоконфликт при записи в БД)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ValueError(f"Ожидается boolean, получено: {value!r}")


def _as_float(value: Any) -> float | None:
    """Число/None, иначе ``ValueError`` (не-число упадёт в Float-колонке)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Ожидается число, получено: {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Ожидается число, получено: {value!r}") from None


def _as_str_list(value: Any) -> list[str]:
    """Список строк/None; единственная строка (``okpd_codes: "62"``) не считается
    списком — иначе ``list("62")`` разбил бы её на символы (тихое искажение)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"Ожидается список строк, получено: {value!r}")


def parse_profile_json(content: str) -> dict[str, Any]:
    """JSON-текст файла -> seed для ``upsert_profile``.

    Понимает и плоскую форму (поля профиля в корне), и обёртку ``profile``+``competencies``.
    Поля приводятся к типам колонок; при неверном типе бросается ``ValueError``
    (``clients.py`` откатывается на markdown-парсер).
    """
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект")
    profile = payload.get("profile")
    src = profile if isinstance(profile, dict) else payload
    return {
        "name": str(src.get("name") or "default").strip(),
        "enabled": _as_bool(src.get("enabled")),
        "is_active": _as_bool(src.get("is_active")),
        "competencies": _join_competencies(payload.get("competencies")),
        "keywords": _as_str_list(src.get("keywords")),
        "exclusion_words": _as_str_list(src.get("exclusion_words")),
        "questions": src.get("questions") if isinstance(src.get("questions"), list) else [],
        "target_etp": _as_str_list(src.get("target_etp")),
        "target_laws": _as_str_list(src.get("target_laws")),
        "min_fit_threshold": _as_float(src.get("min_fit_threshold")),
        "okpd_codes": _as_str_list(src.get("okpd_codes")),
        "nmck_min": _as_float(src.get("nmck_min")),
        "nmck_max": _as_float(src.get("nmck_max")),
    }
