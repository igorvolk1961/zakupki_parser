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

    Ищем в полном тексте ТЗ фрагмент, совпадающий с обрезанной частью описания
    закупки (subject). Если найден — возвращаем расширенный заголовок: полную
    строку ТЗ, содержащую этот фрагмент (фрагмент + продолжение до переноса
    строки). Если фрагмент не найден — возвращаем ``None`` (тогда используется
    весь текст ТЗ).
    """
    if not description or not tz_text:
        return None
    # За основу берём subject — первую строку описания (обрезанную многоточием).
    first_line = description.splitlines()[0] if description.splitlines() else description
    subject = _TRUNCATION_RE.sub("", _collapse(first_line)).rstrip()
    if not subject:
        return None

    # Ищем фрагмент описания по строкам ТЗ (заголовок обычно одна строка).
    subject_words = subject.split()
    for cut in range(len(subject_words), 0, -1):
        prefix = _collapse(" ".join(subject_words[:cut]))
        for raw_line in tz_text.splitlines():
            line = _collapse(raw_line)
            if prefix and line.startswith(prefix):
                return line
    return None


# Маркеры начала разделов ТЗ (для выделения первой секции как описания закупки).
_SECTION_MARKER_RE = re.compile(
    r"^\s*(?:раздел[^\n]*|общие положения[^\n]*|требования[^\n]*|"
    r"общие сведения[^\n]*|\d+(?:\.\d+)*[\.\)]?\s)",
    re.IGNORECASE,
)


def first_tz_section(text: str, max_chars: int = 2000) -> str:
    """Первая секция текста ТЗ как описание закупки (без глубокого чтения тела).

    Полный текст ТЗ стадия Fit НЕ обрабатывает: берётся первая секция (до первого
    маркера раздела — «Раздел», «Общие положения», «Требования», нумерация «1.»
    и т.п.). При отсутствии маркеров — первые ``max_chars`` символов.
    """
    if not text:
        return ""
    lines = text.splitlines()
    section: list[str] = []
    for line in lines:
        if section and _SECTION_MARKER_RE.match(line):
            break
        section.append(line)
    head = "\n".join(section).strip()
    if not head:
        head = text.strip()
    return head[:max_chars]
