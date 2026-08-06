"""Нормализация заказчиков и ИНН (ADR-4)."""

from __future__ import annotations

import re

_SPACES_RE = re.compile(r"\s+")
_INN_IN_LINK_RE = re.compile(r"inn=(\d{10,12})")


def normalize_name(name: str | None) -> str:
    """Нормализованное наименование заказчика для дедупликации.

    Консервативная нормализация: `casefold`, обрезка и схлопывание внутренних
    пробелов в один. Организационно-правовые формы («ООО/АО» и т.п.) не трогаем,
    чтобы не сливать разные организации — это будущий рефайнмент.
    Должна давать тот же результат, что SQL-нормализация в миграции
    (lower(trim(regexp_replace(x, '\\s+', ' ', 'g')))).
    """
    if not name:
        return ""
    collapsed = _SPACES_RE.sub(" ", name.strip())
    return collapsed.casefold()


def extract_inn_from_link(url: str | None) -> str | None:
    """ИНН из ссылки на организацию (ЕИС 223-ФЗ): ``inn=(\\d{10,12})``."""
    if not url:
        return None
    m = _INN_IN_LINK_RE.search(url)
    return m.group(1) if m else None
