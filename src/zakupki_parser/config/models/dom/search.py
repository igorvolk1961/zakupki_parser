"""Модели URL-фильтра списка закупок (search) и маппинга критериев."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FilterMapping(BaseModel):
    """Привязка критерия к bracket-фильтру реестрового API.

    Формат запроса (например, реестр lot-online ``/etp_back/procedure/list``):

        filter[<key>][condition]=<condition>
        filter[<key>][property]=<property>
        filter[<key>][value][N]=<значение>

    ``value_prefix`` — префикс значения (например, ``_`` для ОКПД2 в sphinx-индексе
    lot-online: ``filter[okpd2][value]=_62.02.2``).
    """

    key: str = Field(description="ключ фильтра (например status, okpd2, '*')")
    property: str = Field(description="свойство фильтра (например status, okpd2, '*')")
    condition: str = Field(description="условие (например match, in, eq)")
    value_prefix: str | None = Field(
        default=None, description="префикс каждого значения (например '_' для ОКПД2)"
    )


class CriteriaMapping(BaseModel):
    """Привязка одного ОБОБЩЁННОГО критерия поиска к конкретному запросу площадки.

    Критерии задаются в config_service.yaml (SearchCriteria) в бизнес-терминах;
    здесь каждый из них маппится на то, куда его подставить в запрос площадки:
      - ``json_path`` — точечный путь внутри ``filter_json`` (напр.
        ``needSpecificFilter.okpdPaths``, ``priceFrom``);
      - ``query_param`` — имя плоского query-параметра (напр. ``searchString``,
        ``priceFrom``, ``publishDateFrom``);
      - ``filter`` — bracket-фильтр реестрового API (см. ``FilterMapping``).
    Можно указать оба сразу (например, критерий попадает и в JSON, и в параметр).
    Ключ словаря criteria_map — один из известных критериев: ``publish_date``,
    ``update_date``, ``deadline_from``, ``okpd2``, ``nmck_min``, ``nmck_max``,
    ``keywords``.
    """

    filter: FilterMapping | None = Field(
        default=None,
        description="bracket-фильтр API вида filter[<key>][...] (реестр lot-online)",
    )
    json_path: str | None = Field(
        default=None, description="точечный путь внутри filter_json для значения"
    )
    query_param: str | None = Field(
        default=None, description="имя плоского query-параметра для значения"
    )
    query_params: dict[str, str] | None = Field(
        default=None,
        description=(
            "несколько query-параметров (значения — шаблоны с плейсхолдерами "
            "{okpd2_ids}/{okpd2_codes}); используется для ОКПД2 на ЕИС "
            "(okpd2Ids + okpd2IdsCodes)"
        ),
    )
    raw_array: str | None = Field(
        default=None,
        description=(
            "базовое имя query-параметра-массива, в который передаются значения "
            "как есть (raw) с индексами: `<name>[0]=...&<name>[1]=...`. "
            "Используется для ОКПД2 на площадках, которые матчат код по префиксу "
            "(вложенные коды включаются сервером, дерево не нужно), например ЭТП ГПБ "
            "(procedure[okpd][0]=62.02)"
        ),
    )
    raw_array_flat: str | None = Field(
        default=None,
        description=(
            "базовое имя query-параметра-массива, который передаётся ПОВТОРЯЮЩИМИСЯ "
            "параметрами без индекса: `name=value&name=value`. Отличается от raw_array "
            "(там `name[N]=value`). Используется для ОКПД2 на lot-online (okpd2=62.02; "
            "индексная форма там игнорируется)"
        ),
    )
    json_value: bool = Field(
        default=False,
        description=(
            "JSON-кодировать значение перед подстановкой в query_param (например, "
            'searchToken="фраза" — строка в кавычках, с экранированием)'
        ),
    )


class SearchFilterConfig(BaseModel):
    """URL-фильтр списка закупок — полностью конфигурируемый.

    Статичная часть запроса задаётся ``query_params`` (имена и шаблоны значений с
    плейсхолдерами ``{filter_json}``/``{state_json}``), структуры JSON-параметров
    ``filter_json``/``state_json``, а формат даты порога — ``date_great_equal_format``.

    ОБОБЩЁННЫЕ критерии из config_service.yaml (SearchCriteria) подставляются через
    ``criteria_map``: ключ критерия -> куда его положить (JSON-путь и/или query-параметр).
    Это убирает зависимость кода от конкретных имён параметров площадки.
    """

    enabled: bool = Field(default=True)
    api_endpoint: str | None = Field(
        default=None,
        description=(
            "если задан — список закупок получается GET-запросом к этому API-эндпоинту "
            "(относительный путь, например /api/v2/procedures/) вместо парсинга DOM-страницы "
            "list_path. Query строится так же (query_params + criteria_map), фильтрацию выполняет "
            "сервер (etpgpb: SPA-страница рендерит базовый список, фильтрует только API). "
            "Ответ — JSON {data: [{id, attributes}]}."
        ),
    )
    api_items_key: str | None = Field(
        default=None,
        description=(
            "ключ массива записей внутри ответа API списка: None — массив в data "
            "(etpgpb), иначе data[api_items_key] (например lot-online: data.items; "
            "data.count — общее число результатов)"
        ),
    )
    api_item_format: Literal["etpgpb", "lot_online", "mos", "tender_223"] = Field(
        default="etpgpb",
        description="формат item'а списка для парсинга в карточку записи",
    )
    api_offset_step: int | None = Field(
        default=None,
        description=(
            "шаг постраничной выдачи API списка (offset): None — page-пагинация "
            "шагом 1 (etpgpb); задан — offset += api_offset_step на каждой странице "
            "(lot-online: offset=0,10,20 при limit=10)"
        ),
    )
    api_offset_param: str | None = Field(
        default=None,
        description=(
            "имя плейсхолдера offset в шаблонах query_params (например skip для "
            "mos.ru, где пагинация take/skip внутри параметра queryDto). При задан "
            "URL перестраивается с новым offset на каждой странице (вместо "
            "инкремента плоского параметра)"
        ),
    )
    query_params: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description=(
            "имя параметра запроса -> шаблон значения; плейсхолдеры {filter_json}, {state_json}. "
            "Значение может быть списком — тогда параметр повторяется (status[]=2&status[]=3)"
        ),
    )
    filter_json: dict[str, Any] = Field(
        default_factory=dict, description="статичная структура параметра filter (JSON)"
    )
    state_json: dict[str, Any] = Field(
        default_factory=dict, description="статичная структура параметра state (JSON)"
    )
    date_great_equal_format: str = Field(
        default="%d.%m.%Y 00:00:00",
        description="формат даты порога (критерий publish_date)",
    )
    okpd_tree_file: str | None = Field(
        default=None,
        description="путь к маппингу ОКПД2 (код -> путь) для этой площадки",
    )
    criteria_map: dict[str, CriteriaMapping] = Field(
        default_factory=dict,
        description=(
            "обобщённый критерий (publish_date|update_date|deadline_from|okpd2|"
            "nmck_min|nmck_max|active_only) -> привязка к "
            "запросу площадки (JSON-путь и/или query-параметр)"
        ),
    )
    state_ids: dict[str, list[int | str]] | None = Field(
        default=None,
        description=(
            "внутренние ID состояний закупок площадки для фильтра active_only "
            "(например {'active': [19000002, 19000008], 'all': [0, 1, 2]}). "
            "'active' — состояния активных закупок (подставляется при "
            "search_criteria.active_only=true); 'all' — полный набор состояний "
            "(подставляется при active_only=false, если задан; если не задан — "
            "параметр не ставится, площадка возвращает выдачу по умолчанию). "
            "Путь/параметр, куда подставить, задаётся в criteria_map для ключа "
            "active_only (json_path, query_param или raw_array_flat)"
        ),
    )
