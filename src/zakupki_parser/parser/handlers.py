"""Обработчики значений переменных, извлечённых из DOM.

Чистые функции — легко покрываются unit-тестами.
"""

from __future__ import annotations

import re
from datetime import datetime
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


HANDLERS: dict[str, Any] = {
    "none": lambda v: v,
    "strip": handler_strip,
    "lower": handler_lower,
    "money": handler_money,
    "float": handler_float,
    "int": handler_int,
    "date_iso": handler_date_iso,
}


def apply_handler(name: str | None, value: Any) -> Any:
    if not name:
        return value
    handler = HANDLERS.get(name, HANDLERS["none"])
    return handler(value)
