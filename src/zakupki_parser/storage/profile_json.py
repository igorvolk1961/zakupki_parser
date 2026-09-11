"""Сериализация профиля в единый JSON-файл (экспорт/импорт) с подобъектом компетенций.

Формат — один файл без внешних ссылок: компетенции всегда внутри (подобъект),
поэтому файл можно выгрузить и повторно загрузить целиком. Для структурированного
редактора компетенций подобъект повторяет модель ``scoring_service.profile.Profile``
(``positioning``, ``breadth``, ``competencies[]``, ``exclusions``, ``scoring_policy``) —
именно эта структура при сохранении в БД (строка ``profile.competencies``) понимается
фронтендом (``parseComp``) и scoring-воркером (``profile_to_texts``). Legacy-текст
представляется как ``{"mode": "raw", "text": ...}``.

Файл также несёт факты профиля BR-03 — ``licenses`` и ``experience``. Для переносимости
между БД ссылки на справочники хранятся стабильными ключами, а не числовыми id:
- ``licenses[].license_type_name`` — уникальное наименование вида лицензии
  (``license_types.name``, сид ``licenze_kind.md``);
- ``experience[].confirmation_type_code`` — стабильный код типа подтверждения
  (``experience_confirmation_types.code``: ``platform``/``documents``/``registry``).

При импорте эти ключи резолвятся в ``license_type_id``/``confirmation_type_id``
(``resolve_profile_fact_refs``) перед записью в БД.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from zakupki_parser.storage.competencies import normalize_competencies

SCHEMA = "zakupki-profile"
VERSION = 1

# Колонки ORM-моделей ProfileLicense/ProfileExperience (BR-03). Только эти ключи
# передаются в ``upsert_profile`` (без id/profile_id/created_at/updated_at).
_LICENSE_FIELDS = (
    "license_type_id",
    "number",
    "authority",
    "issue_date",
    "expiry_date",
    "notes",
)
_EXPERIENCE_FIELDS = (
    "title",
    "customer_name",
    "contract_number",
    "start_date",
    "end_date",
    "amount",
    "confirmation_type_id",
    "import_independent",
    "notes",
)


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


def _iso(value: Any) -> str | None:
    """date/datetime -> ISO-строка; ``None`` -> ``None``; прочее приводим к строке."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _fact_ref(entry: Mapping[str, Any], *, field: str, nested: str, key: str) -> Any:
    """Ссылка на справочник из записи факта.

    Берёт ``entry[f"{nested}.{field}"]`` (объект ``_out`` со вложенным справочником),
    затем ``entry[key]`` (плоская переносимая форма ``license_type_name``/
    ``confirmation_type_code``), затем ``entry[f"{nested}_id"]`` (числовой id).
    """
    inner = entry.get(nested)
    if isinstance(inner, Mapping) and inner.get(field) is not None:
        return inner[field]
    if entry.get(key) is not None:
        return entry[key]
    return entry.get(f"{nested}_id")


def _serialize_license(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Запись лицензии -> переносимая форма (``license_type_name`` вместо id)."""
    return {
        "license_type_id": entry.get("license_type_id"),
        "license_type_name": _fact_ref(
            entry, field="name", nested="license_type", key="license_type_name"
        ),
        "number": entry.get("number"),
        "authority": entry.get("authority"),
        "issue_date": _iso(entry.get("issue_date")),
        "expiry_date": _iso(entry.get("expiry_date")),
        "notes": entry.get("notes"),
    }


def _serialize_experience_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Запись опыта -> переносимая форма (``confirmation_type_code`` вместо id)."""
    return {
        "confirmation_type_id": entry.get("confirmation_type_id"),
        "confirmation_type_code": _fact_ref(
            entry, field="code", nested="confirmation_type", key="confirmation_type_code"
        ),
        "title": entry.get("title"),
        "customer_name": entry.get("customer_name"),
        "contract_number": entry.get("contract_number"),
        "start_date": _iso(entry.get("start_date")),
        "end_date": _iso(entry.get("end_date")),
        "amount": entry.get("amount"),
        "import_independent": entry.get("import_independent"),
        "notes": entry.get("notes"),
    }


def _serialize_licenses(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [_serialize_license(e) for e in items if isinstance(e, Mapping)]


def _serialize_experience(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [_serialize_experience_entry(e) for e in items if isinstance(e, Mapping)]


def serialize_profile_json(profile: dict[str, Any]) -> str:
    """``ProfileOut.model_dump()`` -> JSON-текст файла, компетенции как подобъект.

    Факты BR-03 (``licenses``/``experience``) сериализуются переносимыми ссылками
    (наименование/license_type, код/confirmation_type) и при отсутствии — пустыми
    списками (файл самодостаточен, round-trip без потерь).
    """
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
            "target_regions": profile.get("target_regions") or [],
            "max_region_distance_km": profile.get("max_region_distance_km"),
            "keywords": profile.get("keywords") or [],
            "exclusion_words": profile.get("exclusion_words") or [],
            "search_in_documents": bool(profile.get("search_in_documents") or False),
            "questions": profile.get("questions") or [],
            "licenses": _serialize_licenses(profile.get("licenses") or []),
            "experience": _serialize_experience(profile.get("experience") or []),
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


def _as_int(value: Any) -> int | None:
    """Целое/None; строка-цифра приводится к int, иначе ``ValueError`` (FK-колонка)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Ожидается целое число, получено: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Ожидается целое число, получено: {value!r}") from None


def _as_date(value: Any) -> date | None:
    """Дата/None. Строка ISO ``YYYY-MM-DD`` -> ``date`` (asyncpg не принимает str
    для DATE-колонок); дата возвращается как есть; иначе ``ValueError``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            raise ValueError(f"Ожидается дата ISO (YYYY-MM-DD), получено: {value!r}") from None
    raise ValueError(f"Ожидается дата, получено: {value!r}")


def _as_str_list(value: Any) -> list[str]:
    """Список строк/None; единственная строка (``okpd_codes: "62"``) не считается
    списком — иначе ``list("62")`` разбил бы её на символы (тихое искажение)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"Ожидается список строк, получено: {value!r}")


def _fact_entries(value: Any, fields: tuple[str, ...]) -> list[dict[str, Any]] | None:
    """Читает список фактов (licenses/experience) из файла.

    Возвращает ``None``, если ключ не задан или не список (импорт не трогает факты);
    иначе список словарей, приведённых к колонкам ``fields`` (лишние ключи отброшены).
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("Ожидается список")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Ожидается объект")
        out.append(
            {key: item.get(key) for key in fields}
            | {extra: item.get(extra) for extra in ("license_type_name",) if extra in item}
            | {extra: item.get(extra) for extra in ("confirmation_type_code",) if extra in item}
        )
    return out


def parse_profile_json(content: str) -> dict[str, Any]:
    """JSON-текст файла -> seed для ``upsert_profile``.

    Понимает и плоскую форму (поля профиля в корне), и обёртку ``profile``+``competencies``.
    Поля приводятся к типам колонок; при неверном типе бросается ``ValueError``
    (``clients.py`` откатывается на markdown-парсер). Факты BR-03 (``licenses``/
    ``experience``) возвращаются в переносимой форме (name/code) — резолв в id
    выполняет ``resolve_profile_fact_refs``.
    """
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект")
    profile = payload.get("profile")
    src = profile if isinstance(profile, dict) else payload
    seed: dict[str, Any] = {
        "name": str(src.get("name") or "default").strip(),
        "enabled": _as_bool(src.get("enabled")),
        "is_active": _as_bool(src.get("is_active")),
        "competencies": _join_competencies(payload.get("competencies")),
        "keywords": _as_str_list(src.get("keywords")),
        "exclusion_words": _as_str_list(src.get("exclusion_words")),
        "questions": src.get("questions") if isinstance(src.get("questions"), list) else [],
        "target_etp": _as_str_list(src.get("target_etp")),
        "target_laws": _as_str_list(src.get("target_laws")),
        "target_regions": _as_str_list(src.get("target_regions")),
        "max_region_distance_km": _as_float(src.get("max_region_distance_km")),
        "min_fit_threshold": _as_float(src.get("min_fit_threshold")),
        "okpd_codes": _as_str_list(src.get("okpd_codes")),
        "nmck_min": _as_float(src.get("nmck_min")),
        "nmck_max": _as_float(src.get("nmck_max")),
        "search_in_documents": _as_bool(src.get("search_in_documents")) or False,
    }
    # Факты BR-03: ключ задан явно — импортируем (полная замена), иначе не трогаем.
    licenses = _fact_entries(src.get("licenses"), _LICENSE_FIELDS)
    if licenses is not None:
        seed["licenses"] = licenses
    experience = _fact_entries(src.get("experience"), _EXPERIENCE_FIELDS)
    if experience is not None:
        seed["experience"] = experience
    return seed


def _resolve_license(
    entry: Mapping[str, Any], license_name_to_id: Mapping[str, int]
) -> dict[str, Any]:
    """Переносимая запись лицензии -> колонки ``ProfileLicense``.

    Ссылка ``license_type_name`` резолвится в ``license_type_id``; даты приводятся
    к ``date`` (asyncpg не принимает строку для DATE-колонок).
    """
    name = entry.get("license_type_name")
    if name:
        type_id = license_name_to_id.get(str(name))
        if type_id is None:
            raise ValueError(f"Неизвестный вид лицензии: {name}")
        entry = {**entry, "license_type_id": type_id}
    return {
        "license_type_id": _as_int(entry.get("license_type_id")),
        "number": entry.get("number"),
        "authority": entry.get("authority"),
        "issue_date": _as_date(entry.get("issue_date")),
        "expiry_date": _as_date(entry.get("expiry_date")),
        "notes": entry.get("notes"),
    }


def _resolve_experience(
    entry: Mapping[str, Any], confirmation_code_to_id: Mapping[str, int]
) -> dict[str, Any]:
    """Переносимая запись опыта -> колонки ``ProfileExperience``.

    Ссылка ``confirmation_type_code`` резолвится в ``confirmation_type_id``; даты
    приводятся к ``date``, сумма — к ``float``, флаг — к ``bool``.
    """
    code = entry.get("confirmation_type_code")
    if code:
        type_id = confirmation_code_to_id.get(str(code))
        if type_id is None:
            raise ValueError(f"Неизвестный тип подтверждения: {code}")
        entry = {**entry, "confirmation_type_id": type_id}
    return {
        "title": entry.get("title"),
        "customer_name": entry.get("customer_name"),
        "contract_number": entry.get("contract_number"),
        "start_date": _as_date(entry.get("start_date")),
        "end_date": _as_date(entry.get("end_date")),
        "amount": _as_float(entry.get("amount")),
        "confirmation_type_id": _as_int(entry.get("confirmation_type_id")),
        "import_independent": _as_bool(entry.get("import_independent")),
        "notes": entry.get("notes"),
    }


def resolve_profile_fact_refs(
    seed: dict[str, Any],
    license_name_to_id: Mapping[str, int],
    confirmation_code_to_id: Mapping[str, int],
) -> dict[str, Any]:
    """Резолвит ссылки лицензий/опыта в ``seed`` в id справочников.

    Применяется перед ``upsert_profile`` (иначе переносимые name/code не записать
    в FK-колонки). При неизвестной ссылке бросается ``ValueError``.
    """
    seed = dict(seed)
    if "licenses" in seed:
        seed["licenses"] = [
            _resolve_license(e, license_name_to_id) for e in seed.get("licenses") or []
        ]
    if "experience" in seed:
        seed["experience"] = [
            _resolve_experience(e, confirmation_code_to_id) for e in seed.get("experience") or []
        ]
    return seed
