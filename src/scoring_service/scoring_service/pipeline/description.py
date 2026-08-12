"""Извлечение описания закупки из карточки.

На первом этапе сервис НЕ использует тексты документов — только описание закупки
(``subject`` + разумный набор полей ``detail_json``).
"""

from __future__ import annotations

from typing import Any

_DETAIL_FIELDS = (
    "subject",
    "customer",
    "okpd2_codes",
    "okpd2_code",
    "okpd2_name",
    "kpgz_codes",
    "nmck",
    "law",
    "region",
    "deadline",
    "execution_term",
)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return " ".join(text.split())


def extract_description(record: dict[str, Any]) -> str:
    """Собрать человекочитаемое описание закупки из карточки.

    Приоритет — ``subject``; дополняется ключевыми полями из ``detail_json``.
    """
    parts: list[str] = []

    subject = _clean(record.get("subject"))
    if subject:
        parts.append(subject)

    detail = record.get("detail_json")
    if isinstance(detail, dict):
        for field in _DETAIL_FIELDS:
            if field == "subject":
                continue
            value = _clean(detail.get(field))
            if value:
                parts.append(f"{field}: {value}")

    # Фолбэк: берём известные плоские поля карточки
    for field in _DETAIL_FIELDS:
        if field == "subject":
            continue
        value = _clean(record.get(field))
        if value and not any(f"{field}:" in part for part in parts):
            parts.append(f"{field}: {value}")

    return "\n".join(parts) if parts else "(описание отсутствует)"
