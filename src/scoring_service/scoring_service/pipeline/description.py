"""Извлечение описания закупки из карточки.

На первом этапе сервис НЕ использует тексты документов — только описание закупки
(``subject`` + разумный набор полей ``detail_json``).

Здесь же — вспомогательные функции для обработки обрезанного (многоточием)
описания: ``is_truncated_description`` и алгоритмическое расширение заголовка
описания из полного текста ТЗ (``extend_description_from_tz``).
"""

from __future__ import annotations

import re
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

# Маркеры обрезанного описания: «…», три точки (в т.ч. «..» в конце).
_TRUNCATION_RE = re.compile(r"(?:\.{2,}|…)\s*$")


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


def is_truncated_description(description: str) -> bool:
    """Заканчивается ли описание закупки многоточием (обрезано ли).

    Срезаться многоточием может любой фрагмент (обычно subject — первая строка),
    поэтому проверяем каждую непустую строку описания.
    """
    if not description:
        return False
    return any(_TRUNCATION_RE.search(line) for line in description.splitlines() if line.strip())


def _collapse(text: str) -> str:
    """Нормализовать пробелы для поиска фрагмента (без смены регистра)."""
    return " ".join(text.split())


def extend_description_from_tz(description: str, tz_text: str) -> str | None:
    """Алгоритмически расширить обрезанное описание фрагментом из текста ТЗ.

    Ищем строку ТЗ, СОДЕРЖАЩУЮ префикс слов обрезанного описания закупки
    (subject), перебирая префиксы от самого длинного к короткому. Если найдена —
    возвращаем расширенный заголовок: полную строку ТЗ (префикс + продолжение
    до переноса строки). Если ни один префикс не найден — возвращаем ``None``
    (остаётся исходное описание из карточки).
    """
    if not description or not tz_text:
        return None
    # За основу берём subject — первую строку описания (обрезанную многоточием).
    first_line = description.splitlines()[0] if description.splitlines() else description
    subject = _TRUNCATION_RE.sub("", _collapse(first_line)).rstrip()
    if not subject:
        return None

    # Ищем строку ТЗ, содержащую префикс слов subject (заголовок обычно одна
    # строка и может начинаться с markdown-разметки «#»).
    subject_words = subject.split()
    for cut in range(len(subject_words), 0, -1):
        prefix = _collapse(" ".join(subject_words[:cut]))
        for raw_line in tz_text.splitlines():
            line = _collapse(raw_line)
            if prefix and prefix in line:
                return line
    return None
