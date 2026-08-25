"""Обработчики значений переменных, извлечённых из DOM.

Чистые функции — легко покрываются unit-тестами. Разбиты по доменам
(подпакеты): ``strings`` (strip/lower/regex/purchase_type/law), ``money``
(денежные/числовые), ``dates`` (ISO/МСК-даты), ``registry`` (реестр и
диспетчер ``apply_handler``). Здесь — реэкспорт для совместимости с прежним
модулем ``parser/handlers.py``.
"""

from __future__ import annotations

from zakupki_parser.parser.handlers.dates import (
    MSK,
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
from zakupki_parser.parser.handlers.registry import HANDLERS, apply_handler
from zakupki_parser.parser.handlers.strings import (
    handler_law,
    handler_lower,
    handler_purchase_type,
    handler_regex,
    handler_strip,
)

__all__ = [
    "MSK",
    "HANDLERS",
    "apply_handler",
    "handler_date",
    "handler_date_iso",
    "handler_datetime",
    "handler_deadline",
    "handler_float",
    "handler_int",
    "handler_law",
    "handler_lower",
    "handler_money",
    "handler_pub_date",
    "handler_purchase_type",
    "handler_regex",
    "handler_regex_datetime",
    "handler_ru_date",
    "handler_security",
    "handler_security_unit",
    "handler_strip",
]
