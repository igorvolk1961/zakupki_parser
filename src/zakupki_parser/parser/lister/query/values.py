"""Вычисление значений обобщённых критериев поиска (publish_date, okpd2, НМЦК…)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from zakupki_parser.config.models import SearchCriteria, SearchFilterConfig
from zakupki_parser.parser.lister.query.okpd2 import _resolve_paths

logger = logging.getLogger(__name__)

# Площадка работает в часовом поясе МСК (UTC+3) — даты фильтра в этом поясе.
MSK = timezone(timedelta(hours=3))


def _set_json_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Вставляет ``value`` в ``data`` по точечному пути ``path`` (создаёт словари)."""
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _value_to_str(value: Any) -> str:
    """Приводит значение критерия к строке для query-параметра."""
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    if isinstance(value, float):
        return str(int(value) if value.is_integer() else value)
    return str(value)


def _criteria_value(
    key: str,
    criteria: SearchCriteria,
    cutoff: datetime | None,
    search: SearchFilterConfig,
) -> Any:
    """Вычисляет значение обобщённого критерия по его ключу.

    Возвращает None (или пустой список), если критерий не задан — такой критерий
    пропускается и в запрос не попадает.
    """
    if key == "publish_date":
        if cutoff is None:
            return None
        return cutoff.astimezone(MSK).strftime(search.date_great_equal_format)
    if key == "update_date":
        # Дата «Обновлено» (ЕИС updateDateFrom) — тот же порог cutoff.
        if cutoff is None:
            return None
        return cutoff.astimezone(MSK).strftime(search.date_great_equal_format)
    if key == "deadline_from":
        # Срок подачи заявок не раньше сегодня (закупки с просроченным дедлайном
        # отсекаются сервером — дополняет search_criteria.deadline_not_expired).
        return datetime.now(MSK).strftime(search.date_great_equal_format)
    if key == "okpd2":
        if not criteria.okpd_codes:
            return []
        # Без дерева поиска коды передаются как есть (например, etpgpb с плоским
        # procedure[okpd]=... и префиксным матчингом сервера); иначе — пути из дерева.
        if not search.okpd_tree_file:
            return criteria.okpd_codes
        return _resolve_paths(criteria.okpd_codes, search.okpd_tree_file, "ОКПД2")
    if key == "nmck_min":
        return criteria.nmck_min
    if key == "nmck_max":
        return criteria.nmck_max
    logger.warning("Неизвестный критерий поиска в criteria_map: %s", key)
    return None
