"""Реестр обработчиков значений (имя -> функция) и диспетчер ``apply_handler``."""

from __future__ import annotations

from typing import Any

from zakupki_parser.parser.handlers.dates import (
    handler_date,
    handler_date_iso,
    handler_datetime,
    handler_deadline,
    handler_pub_date,
    handler_regex_datetime,
    handler_ru_date,
)
from zakupki_parser.parser.handlers.money import (
    handler_float,
    handler_int,
    handler_money,
    handler_security,
    handler_security_unit,
)
from zakupki_parser.parser.handlers.strings import (
    handler_law,
    handler_lower,
    handler_purchase_type,
    handler_regex,
    handler_strip,
)

HANDLERS: dict[str, Any] = {
    "none": lambda v: v,
    "strip": handler_strip,
    "lower": handler_lower,
    "money": handler_money,
    "float": handler_float,
    "int": handler_int,
    "date_iso": handler_date_iso,
    "date": handler_date,
    "datetime": handler_datetime,
    "regex_datetime": handler_regex_datetime,
    "ru_date": handler_ru_date,
    "pub_date": handler_pub_date,
    "deadline": handler_deadline,
    "law": handler_law,
    "purchase_type": handler_purchase_type,
    "regex": handler_regex,
    "security": handler_security,
    "security_unit": handler_security_unit,
}


def apply_handler(name: str | None, value: Any, arg: str | None = None) -> Any:
    if not name:
        return value
    handler = HANDLERS.get(name, HANDLERS["none"])
    # Обработчики, которым нужен аргумент (regex-паттерн).
    if name in ("regex", "regex_datetime"):
        return handler(value, arg)
    return handler(value)
