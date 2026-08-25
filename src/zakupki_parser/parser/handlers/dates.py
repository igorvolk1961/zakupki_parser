"""Обработчики дат: ISO/МСК-форматы, русские месяцы, «с …»/«до …»."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from zakupki_parser.parser.handlers.strings import handler_strip

_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y в %H:%M",
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
)

# Часовой пояс площадки — МСК (UTC+3). Даты в строках «с … до … (МСК)».
MSK = timezone(timedelta(hours=3))

_RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

_PUB_DATE_RE = re.compile(r"с\s+(\d{2}\.\d{2}\.\d{4})")
_DEADLINE_RE = re.compile(r"до\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}:\d{2})")


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


def handler_datetime(value: Any) -> datetime | None:
    """Дата со временем «ДД.ММ.ГГГГ [ЧЧ:ММ[:СС]]» -> aware datetime (МСК).

    Для чистых дат-колонок на площадках с серверной выдачей (напр. B2B-Center).
    Без времени — полночь.
    """
    if value is None:
        return None
    text = handler_strip(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=MSK)
        except ValueError:
            continue
    return None


def handler_regex_datetime(value: Any, arg: str | None = None) -> datetime | None:
    """Извлекает дату/время regex-паттерном (группа 1) и парсит в aware datetime (МСК).

    Для полей-текстов вида «Опубликована 12.08.2026 00:55», «12.08.2026 00:55 - …».
    """
    if value is None or not arg:
        return None
    m = re.search(arg, str(value))
    if not m:
        return None
    text = m.group(1).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=MSK)
        except ValueError:
            continue
    return None


def handler_ru_date(value: Any) -> datetime | None:
    """Дата «ДД месяц ГГГГ[, ЧЧ:ММ]» (русские месяцы) -> aware datetime (МСК).

    Напр. «14 августа 2026, 18:00 МСК». Без времени — полночь.
    """
    if value is None:
        return None
    text = handler_strip(value)
    m = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})\s*(?:,\s*(\d{1,2}:\d{2}))?", text)
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    month = _RU_MONTHS.get(month_name)
    if month is None:
        return None
    try:
        if m.group(4):
            return datetime.strptime(
                f"{day:02d}.{month:02d}.{year} {m.group(4)}", "%d.%m.%Y %H:%M"
            ).replace(tzinfo=MSK)
        return datetime(year, month, day).replace(tzinfo=MSK)
    except ValueError:
        return None


def handler_date(value: Any) -> datetime | None:
    """Дата «ДД.ММ.ГГГГ» -> aware datetime (МСК). Для колонок DateTime."""
    if value is None:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%d.%m.%Y").replace(tzinfo=MSK)
    except ValueError:
        return None


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
