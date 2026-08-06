"""Общие форматтеры дат и денег для имитатора.

Единая точка истины для формата строки дат «с ДД.ММ.ГГГГ до ДД.ММ.ГГГГ HH:MM (МСК)»
(его парсит парсер обработчиками pub_date/deadline) и денежного форматирования.
"""

from __future__ import annotations

import re
from datetime import datetime

# Регулярка парсера для даты публикации (см. handlers._PUB_DATE_RE).
_PUB_DATE_RE = re.compile(r"с\s+(\d{2}\.\d{2}\.\d{4})")

_DATE_STR_FMT = "%d.%m.%Y"


def format_dates(pub: datetime, deadline: datetime) -> str:
    """Строка дат карточки закупки в формате парсера."""
    return f"с {pub:{_DATE_STR_FMT}} до {deadline:{_DATE_STR_FMT}} {deadline:%H:%M} (МСК)"


def parse_publication_date(value: str) -> datetime | None:
    """Дата публикации «с ДД.ММ.ГГГГ …» из строки дат карточки."""
    m = _PUB_DATE_RE.search(value)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), _DATE_STR_FMT)
    except ValueError:
        return None


def format_money(value: float) -> str:
    """Формат «1 234 567,89 ₽» (как на площадке)."""
    return f"{value:,.2f} ₽".replace(",", "\u00a0").replace(".", ",")
