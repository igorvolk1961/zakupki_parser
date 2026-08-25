"""Bracket-фильтры реестрового API (filter[<key>][condition|property|value…])."""

from __future__ import annotations

from datetime import datetime

from zakupki_parser.config.models import CriteriaMapping, SearchCriteria, SearchFilterConfig
from zakupki_parser.parser.lister.query.values import _criteria_value


def _filter_dsl_values(
    key: str,
    mapping: CriteriaMapping,
    criteria: SearchCriteria,
    cutoff: datetime | None,
    search: SearchFilterConfig,
) -> list[str]:
    """Значения критерия для bracket-фильтра API (``mapping.filter``).

    Пустой список — критерий не задан (в запрос не попадает).
    """
    if key == "okpd2":
        codes = criteria.okpd_codes
        if not codes:
            return []
        fm = mapping.filter
        assert fm is not None
        prefix = fm.value_prefix or ""
        return [f"{prefix}{code}" for code in codes]
    if key == "active_only":
        ids = (search.state_ids or {}).get("active" if criteria.active_only else "all")
        return [str(v) for v in ids] if ids else []
    value = _criteria_value(key, criteria, cutoff, search)
    if value is None or (isinstance(value, list) and not value):
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _filter_dsl_params(mapping: CriteriaMapping, values: list[str]) -> list[tuple[str, str]]:
    """Bracket-параметры фильтра: filter[<key>][condition|property|value...].

    Одно значение — ``filter[<key>][value]=<v>``, несколько — индексированные
    ``filter[<key>][value][N]=<v>`` (как шлёт фронтенд lot-online).
    """
    f = mapping.filter
    assert f is not None
    params: list[tuple[str, str]] = [
        (f"filter[{f.key}][condition]", f.condition),
        (f"filter[{f.key}][property]", f.property),
    ]
    if len(values) == 1:
        params.append((f"filter[{f.key}][value]", values[0]))
    else:
        for i, value in enumerate(values):
            params.append((f"filter[{f.key}][value][{i}]", value))
    return params
