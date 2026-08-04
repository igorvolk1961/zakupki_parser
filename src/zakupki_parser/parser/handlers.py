"""Обработчики значений переменных, извлечённых из DOM.

Чистые функции — легко покрываются unit-тестами.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

_MONEY_RE = re.compile(r"[^\d.,\-]")
_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
)


def handler_strip(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def handler_lower(value: Any) -> str:
    return handler_strip(value).lower()


def handler_money(value: Any) -> float | None:
    if value is None:
        return None
    cleaned = _MONEY_RE.sub("", str(value)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def handler_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def handler_int(value: Any) -> int | None:
    if value is None:
        return None
    cleaned = _MONEY_RE.sub("", str(value))
    try:
        return int(cleaned)
    except ValueError:
        return None


def handler_date_iso(value: Any) -> str | None:
    if value is None:
        return None
    text = handler_strip(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return None


# Часовой пояс площадки — МСК (UTC+3). Даты в строках «с … до … (МСК)».
MSK = timezone(timedelta(hours=3))
_PUB_DATE_RE = re.compile(r"с\s+(\d{2}\.\d{2}\.\d{4})")
_DEADLINE_RE = re.compile(r"до\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}:\d{2})")
_LAW_RE = re.compile(r"(44-ФЗ|223-ФЗ)")


def _parse_dt(day: str, time_: str | None) -> datetime:
    fmt = "%d.%m.%Y %H:%M" if time_ else "%d.%m.%Y"
    text = f"{day} {time_}".strip() if time_ else day
    return datetime.strptime(text, fmt).replace(tzinfo=MSK)


def handler_pub_date(value: Any) -> datetime | None:
    """Извлекает дату публикации «с …» из строки дат карточки."""
    if value is None:
        return None
    m = _PUB_DATE_RE.search(str(value))
    if not m:
        return None
    return _parse_dt(m.group(1), None)


def handler_deadline(value: Any) -> datetime | None:
    """Извлекает срок подачи «до … HH:MM» из строки дат карточки."""
    if value is None:
        return None
    m = _DEADLINE_RE.search(str(value))
    if not m:
        return None
    return _parse_dt(m.group(1), m.group(2))


def handler_law(value: Any) -> str | None:
    """Извлекает закон (44-ФЗ / 223-ФЗ) из текста карточки."""
    if value is None:
        return None
    m = _LAW_RE.search(str(value))
    return m.group(0) if m else None


def handler_regex(value: Any, arg: str | None = None) -> str | None:
    """Извлекает первую группу regex-паттерна (``arg``)."""
    if value is None or not arg:
        return None
    m = re.search(arg, str(value))
    return m.group(1) if m else None


HANDLERS: dict[str, Any] = {
    "none": lambda v: v,
    "strip": handler_strip,
    "lower": handler_lower,
    "money": handler_money,
    "float": handler_float,
    "int": handler_int,
    "date_iso": handler_date_iso,
    "pub_date": handler_pub_date,
    "deadline": handler_deadline,
    "law": handler_law,
    "regex": handler_regex,
}


def apply_handler(name: str | None, value: Any, arg: str | None = None) -> Any:
    if not name:
        return value
    handler = HANDLERS.get(name, HANDLERS["none"])
    return handler(value, arg) if name == "regex" else handler(value)
