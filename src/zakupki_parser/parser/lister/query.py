"""Построение URL страницы списка закупок по конфигурации ``search``.

Обобщённые критерии из ``search_criteria`` подставляются в запрос через
``criteria_map``: в ``filter_json`` (по JSON-пути) и/или в плоский
query-параметр. ОКПД2 дополнительно резолвится: коды -> внутренние id
площадки (дерево ``code_to_id``) или передаются как есть (префиксный матчинг).
"""

from __future__ import annotations

import copy
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from zakupki_parser.config.models import (
    CriteriaMapping,
    PlatformDom,
    SearchCriteria,
    SearchFilterConfig,
)
from zakupki_parser.okpd import load_okpd_tree, resolve_codes_to_paths

logger = logging.getLogger(__name__)

# Площадка работает в часовом поясе МСК (UTC+3) — даты фильтра в этом поясе.
MSK = timezone(timedelta(hours=3))


def _resolve_paths(codes: list[str], tree_file: str | None, label: str) -> list[str] | None:
    """Резолвит коды (ОКПД2/регион) в пути через маппинг площадки.

    Возвращает None, если маппинг недоступен (коды не применятся).
    """
    if not codes:
        return []
    if not tree_file:
        logger.warning("%s коды заданы, но search-маппинг не указан", label)
        return None
    try:
        tree = load_okpd_tree(tree_file)
        return resolve_codes_to_paths(codes, tree, label=label)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить %s дерево %s: %s", label, tree_file, exc)
        return None


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def _resolve_okpd2_eis(codes: list[str], tree_file: str | None) -> dict[str, str] | None:
    """Резолвит коды ОКПД2 для ЕИС в параметр ``okpd2Ids``.

    Возвращает ``{"okpd2Ids": ...}`` — только собственные id выбранных кодов.
    Дочерние узлы подключаются флагом ``okpd2IdsWithNested=on`` (в статических
    query_params), поэтому перечислять всё поддерево не нужно. Для кода без
    собственного id берётся ближайший предок.
    """
    if not codes:
        return None
    if not tree_file:
        logger.warning("ОКПД2 коды заданы, но search-маппинг (ЕИС) не указан")
        return None
    try:
        tree = load_okpd_tree(tree_file)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить дерево ОКПД2 ЕИС %s: %s", tree_file, exc)
        return None

    code_to_id = tree.get("code_to_id") or {}
    ids: list[str] = []
    for code in codes:
        cid = code_to_id.get(code)
        if cid is None:
            cid = _nearest_ancestor_id(code, code_to_id)
        if cid and cid not in ids:
            ids.append(cid)
    if not ids:
        return None
    return {"okpd2Ids": ",".join(ids)}


def _nearest_ancestor_id(code: str, code_to_id: dict[str, str]) -> str | None:
    """id ближайшего предка кода (по цифровому префиксу) или None."""
    digits = _digits(code)
    best_len = 0
    best_id: str | None = None
    for c, cid in code_to_id.items():
        key_digits = _digits(c)
        if key_digits and digits.startswith(key_digits) and len(key_digits) > best_len:
            best_len = len(key_digits)
            best_id = cid
    return best_id


def _resolve_okpd2_ids(codes: list[str], tree_file: str | None) -> list[str] | None:
    """Резолвит коды ОКПД2 в внутренние ID площадки для массива ``okpd2[]``.

    Фабрикант фильтрует по ОКПД2 через ``okpd2[]=<opaque-id>`` (не коды):
    id берутся из дерева площадки (``code_to_id``). Код без собственного id
    резолвится в id ближайшего предка (по цифровому префиксу). Если дерево
    не задано (или в нём нет ``code_to_id``) — возвращается None: вызывающий
    передаёт коды как есть (например, etpgpb с префиксным матчингом сервера).
    """
    if not codes:
        return []
    if not tree_file:
        return None
    try:
        tree = load_okpd_tree(tree_file)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить дерево ОКПД2 %s: %s", tree_file, exc)
        return None
    code_to_id = tree.get("code_to_id") or {}
    if not code_to_id:
        return None
    ids: list[str] = []
    for code in codes:
        cid = code_to_id.get(code)
        if cid is None:
            cid = _nearest_ancestor_id(code, code_to_id)
        if cid and cid not in ids:
            ids.append(cid)
    return ids


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


def build_query(
    search: SearchFilterConfig,
    cutoff: datetime | None,
    criteria: SearchCriteria | None = None,
    offset: int = 0,
) -> str:
    """Строит строку запроса по конфигурации ``search``.

    Обобщённые критерии из ``config_service.yaml -> search_criteria`` подставляются
    в запрос через ``search.criteria_map`` (см. ``_criteria_value``): каждый критерий
    уходит либо в ``filter_json`` (по JSON-пути), либо в плоский query-параметр,
    либо в оба места. Не заданные критерии в запрос не попадают. ``offset`` —
    значение плейсхолдера ``{offset}`` в шаблонах ``query_params`` (пагинация
    take/skip, например mos.ru).
    """
    criteria = criteria or SearchCriteria()
    filter_json = copy.deepcopy(search.filter_json)
    state_json = copy.deepcopy(search.state_json)

    extra_params: dict[str, str] = {}
    # Повторяющиеся параметры без индекса (<name>=<v>&<name>=<v>) — для площадок,
    # которые игнорируют индексную форму (например, lot-online okpd2=).
    flat_params: list[tuple[str, str]] = []
    for key, mapping in (search.criteria_map or {}).items():
        if mapping.filter:
            # Bracket-фильтр реестрового API (например lot-online):
            # filter[<key>][condition]=...&filter[<key>][property]=...&filter[<key>][value...]=...
            dsl_values = _filter_dsl_values(key, mapping, criteria, cutoff, search)
            if dsl_values:
                for name, value in _filter_dsl_params(mapping, dsl_values):
                    flat_params.append((name, value))
            continue
        if key == "okpd2" and mapping.query_params:
            values = _resolve_okpd2_eis(criteria.okpd_codes, search.okpd_tree_file)
            if values:
                for param, value in values.items():
                    extra_params[param] = value
            continue
        if key == "okpd2" and (mapping.raw_array or mapping.raw_array_flat):
            # Площадка фильтрует по ОКПД2 массивом параметров. Значения: либо
            # внутренние opaque-id из дерева площадки (fabrikant, okpd2[] — резолв
            # кодов в id через okpd_tree_file.code_to_id), либо сами коды как есть
            # (etpgpb, lot-online: сервер матчит по префиксу, дерево не нужно).
            # Форма: raw_array — индексированная <name>[N]=..., raw_array_flat —
            # повторяющаяся без индекса <name>=...&<name>=... (lot-online okpd2=
            # индексную форму игнорирует).
            codes = criteria.okpd_codes
            if not codes:
                continue
            okpd_ids = _resolve_okpd2_ids(codes, search.okpd_tree_file)
            if okpd_ids is None:
                # Без дерева — коды как есть (с точками), сервер матчит по префиксу.
                okpd_ids = codes
            if mapping.raw_array_flat:
                for value in okpd_ids:
                    flat_params.append((mapping.raw_array_flat, value))
            else:
                for i, value in enumerate(okpd_ids):
                    extra_params[f"{mapping.raw_array}[{i}]"] = value
            continue
        if key == "active_only":
            # Выбор «все/только активные»: только активные подставляет состояния из
            # state_ids.active. При «все» подставляется state_ids.all (если задан),
            # иначе параметр не ставится — площадка отдаёт выдачу по умолчанию.
            # Мульти-параметры (mapping.query_params, например ЕИС af=on&ca=on):
            # при active_only=true ставятся все заданные параметры, иначе — ничего
            # (площадка отдаёт все этапы закупки).
            if mapping.query_params:
                if criteria.active_only:
                    for param, value in mapping.query_params.items():
                        extra_params[param] = value
                continue
            state_ids = search.state_ids or {}
            ids = state_ids.get("active") if criteria.active_only else state_ids.get("all")
            if not ids:
                continue
            if mapping.json_path:
                _set_json_path(filter_json, mapping.json_path, ids)
            if mapping.query_param:
                extra_params[mapping.query_param] = _value_to_str(ids)
            if mapping.raw_array:
                for i, state in enumerate(ids):
                    extra_params[f"{mapping.raw_array}[{i}]"] = str(state)
            if mapping.raw_array_flat:
                for state in ids:
                    flat_params.append((mapping.raw_array_flat, str(state)))
            continue
        value = _criteria_value(key, criteria, cutoff, search)
        if value is None or (isinstance(value, list) and not value):
            continue
        if mapping.json_path:
            _set_json_path(filter_json, mapping.json_path, value)
        if mapping.query_param:
            extra_params[mapping.query_param] = _value_to_str(value)

    filter_json_str = json.dumps(filter_json, ensure_ascii=False, separators=(",", ":"))
    state_json_str = json.dumps(state_json, ensure_ascii=False, separators=(",", ":"))

    parts: list[str] = []
    # Плейсхолдер offset в шаблонах query_params: по умолчанию {offset}, либо
    # имя параметра пагинации (api_offset_param, например {skip} у mos.ru).
    offset_placeholder = "{" + (search.api_offset_param or "offset") + "}"
    for key, template in search.query_params.items():
        param_values = template if isinstance(template, list) else [template]
        for value in param_values:
            value = value.replace("{filter_json}", filter_json_str)
            value = value.replace("{state_json}", state_json_str)
            value = value.replace(offset_placeholder, str(offset))
            # Статические значения (в т.ч. кириллица/пробелы) URL-кодируются целиком.
            parts.append(f"{key}={urllib.parse.quote(value, safe='')}")
    for name, value in extra_params.items():
        parts.append(f"{name}={urllib.parse.quote(value, safe='')}")
    for name, value in flat_params:
        parts.append(f"{name}={urllib.parse.quote(value, safe='')}")
    return "&".join(parts)


def build_list_url(
    platform: PlatformDom,
    cutoff: datetime | None = None,
    criteria: SearchCriteria | None = None,
) -> str:
    """Возвращает URL страницы списка.

    Если у площадки задан ``search`` (URL-фильтр) и он включён — URL с фильтром;
    иначе — простой ``list_path`` (для площадок с DOM-фильтрами).
    """
    base = platform.url.rstrip("/") + platform.list_path
    search = platform.search
    if search is None or not search.enabled:
        return base
    query = build_query(search, cutoff, criteria)
    return f"{base}?{query}"
