"""Обработчики строковых значений (strip/lower/regex/purchase_type)."""

from __future__ import annotations

import re
from typing import Any

_LAW_PREFIX_RE = re.compile(r"^\s*(?:44-ФЗ|223-ФЗ)\s*(?:/|–|—|-)?\s*")
_LAW_RE = re.compile(r"(44-ФЗ|223-ФЗ)")


def handler_law(value: Any) -> str | None:
    """Извлекает закон (44-ФЗ / 223-ФЗ) из текста карточки."""
    if value is None:
        return None
    m = _LAW_RE.search(str(value))
    return m.group(0) if m else None


def handler_strip(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def handler_lower(value: Any) -> str:
    return handler_strip(value).lower()


def handler_regex(value: Any, arg: str | None = None) -> str | None:
    """Извлекает первую группу regex-паттерна (``arg``)."""
    if value is None or not arg:
        return None
    m = re.search(arg, str(value))
    return m.group(1) if m else None


def handler_purchase_type(value: Any) -> str | None:
    """Тип процедуры из текста карточки.

    Убирает префикс закона («44-ФЗ / Электронный аукцион» -> «Электронный
    аукцион», «44-ФЗ\\nЭлектронный аукцион» -> «Электронный аукцион») и
    схлопывает пробелы/переносы строк. Если остаётся пусто — None.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    text = _LAW_PREFIX_RE.sub("", text)
    text = text.strip(" /–—|")
    return text or None
