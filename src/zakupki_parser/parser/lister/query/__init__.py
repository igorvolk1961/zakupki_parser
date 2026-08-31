"""Построение URL страницы списка закупок по конфигурации ``search``.

Обобщённые критерии из ``search_criteria`` подставляются в запрос через
``criteria_map``: в ``filter_json`` (по JSON-пути) и/или в плоский
query-параметр. ОКПД2 дополнительно резолвится: коды -> внутренние id
площадки (дерево ``code_to_id``) или передаются как есть (префиксный матчинг).

Резолв ОКПД2 вынесен в ``okpd2``, вычисление значений критериев — в ``values``,
bracket-фильтры реестрового API — в ``filters_dsl``. Здесь — сборка запроса и
реэкспорт для совместимости с прежним модулем ``lister/query.py``.
"""

from __future__ import annotations

import copy
import json
import logging
import urllib.parse
from datetime import datetime

from zakupki_parser.config.models import PlatformDom, SearchCriteria, SearchFilterConfig
from zakupki_parser.parser.lister.query.filters_dsl import _filter_dsl_params, _filter_dsl_values
from zakupki_parser.parser.lister.query.keywords import (
    MAX_KEYWORD_QUERY_ENC_LEN,
    keyword_batches,
    keyword_search_string,
)
from zakupki_parser.parser.lister.query.okpd2 import _resolve_okpd2_eis, _resolve_okpd2_ids
from zakupki_parser.parser.lister.query.values import (
    MSK,
    _criteria_value,
    _set_json_path,
    _value_to_str,
)

logger = logging.getLogger(__name__)


def build_query(
    search: SearchFilterConfig,
    cutoff: datetime | None,
    criteria: SearchCriteria | None = None,
    offset: int = 0,
    keywords: list[str] | None = None,
) -> str:
    """Строит строку запроса по конфигурации ``search``.

    Обобщённые критерии из ``config_service.yaml -> search_criteria`` подставляются
    в запрос через ``search.criteria_map`` (см. ``_criteria_value``): каждый критерий
    уходит либо в ``filter_json`` (по JSON-пути), либо в плоский query-параметр,
    либо в оба места. Не заданные критерии в запрос не попадают. ``offset`` —
    значение плейсхолдера ``{offset}`` в шаблонах ``query_params`` (пагинация
    take/skip, например mos.ru). ``keywords`` — позитивные слова профиля: если у
    площадки задан ``search.keyword_query_param``, они собираются в строку
    ``(фраза) или слово`` и подставляются в этот параметр (серверная предфильтрация,
    R9; финальная фильтрация по словам остаётся клиентской).
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

    # Серверная предфильтрация по ключевым словам (R9): если у площадки задан
    # keyword_query_param и есть позитивные слова, подставляем строку
    # `(фраза) или слово` (проксимити-выражения приводятся к `(фраза)`, `~N`
    # убирается). Сервер сужает выдачу до потенциально релевантных закупок;
    # финальная фильтрация — клиентская (R9).
    server_keywords = keyword_search_string(keywords or [])
    if server_keywords and search.keyword_query_param:
        encoded_len = len(urllib.parse.quote(server_keywords, safe=""))
        if encoded_len > MAX_KEYWORD_QUERY_ENC_LEN:
            logger.warning(
                "Ключевые слова профиля слишком велики для серверной предфильтрации: "
                "%s=%d симв. после URL-кодирования (порог %d) — параметр %s не подставлен, "
                "предфильтрация пропущена, фильтрация по словам останется клиентской (R9). "
                "Задайте меньший набор слов или дробите обход.",
                search.keyword_query_param,
                encoded_len,
                MAX_KEYWORD_QUERY_ENC_LEN,
                search.keyword_query_param,
            )
        else:
            extra_params[search.keyword_query_param] = server_keywords

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
    keywords: list[str] | None = None,
) -> str:
    """Возвращает URL страницы списка.

    Если у площадки задан ``search`` (URL-фильтр) и он включён — URL с фильтром;
    иначе — простой ``list_path`` (для площадок с DOM-фильтрами). ``keywords`` —
    позитивные слова профиля для серверной предфильтрации (см. ``build_query``).
    """
    base = platform.url.rstrip("/") + platform.list_path
    search = platform.search
    if search is None or not search.enabled:
        return base
    query = build_query(search, cutoff, criteria, keywords=keywords)
    return f"{base}?{query}"


__all__ = [
    "MSK",
    "build_list_url",
    "build_query",
    "keyword_batches",
    "keyword_search_string",
    "_resolve_okpd2_eis",
    "_resolve_okpd2_ids",
]
