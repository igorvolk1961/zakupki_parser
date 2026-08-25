"""Денежные и числовые обработчики значений (money/float/int/security)."""

from __future__ import annotations

import re
from typing import Any

_MONEY_RE = re.compile(r"[^\d.,\-]")


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


def handler_security(value: Any) -> float | None:
    """Обеспечение исполнения контракта на ЕИС.

    Поле может быть процентным («10 %») или рублёвым
    («3 600 239,70 Российский рубль (12,5 %)») — берём первое денежное
    значение (сумму), иначе первое число (процент).
    """
    if value is None:
        return None
    text = str(value)
    m = re.search(r"\d[\d\s]*,\d{2}", text)
    if not m:
        m = re.search(r"\d[\d\s]*", text)
    if not m:
        return None
    return handler_money(m.group(0))


def handler_security_unit(value: Any) -> str | None:
    """Единица измерения обеспечения исполнения контракта.

    Рублёвая сумма («3 600 239,70 …») — «руб.», иначе процент («10 %») — «%».
    """
    if value is None:
        return None
    text = str(value)
    if re.search(r"\d[\d\s]*,\d{2}", text):
        return "руб."
    if "%" in text:
        return "%"
    return None
