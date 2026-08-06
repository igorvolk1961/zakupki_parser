"""Модели DOM-конфигурации площадки и фильтров (config_dom.yaml)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DomVariable(BaseModel):
    """Описание одной извлекаемой переменной."""

    name: str
    selector: str
    attribute: str | None = None
    index: int | None = Field(
        default=None,
        ge=0,
        description="взять N-й элемент, совпавший с селектором (вместо первого)",
    )
    handler: str | None = Field(
        default=None,
        description=(
            "опциональная постобработка: none|strip|float|int|date_iso|lower|"
            "pub_date|deadline|law|regex|money|dates|security"
        ),
    )
    handler_arg: str | None = Field(
        default=None, description="аргумент для обработчика (например, regex-паттерн)"
    )
    default: Any = None


class DomListConfig(BaseModel):
    """Селекторы страницы списка закупок."""

    container: str = Field(description="CSS-селектор контейнера записи о закупке")
    variables: list[DomVariable] = Field(default_factory=list)
    detail_link: str = Field(description="CSS-селектор ссылки на детальную страницу")
    next_page: str = Field(description="CSS-селектор кнопки/ссылки следующей страницы")
    publication_date: str = Field(
        default="publication_date",
        description="имя переменной в list.variables с датой публикации (для стоп-порога)",
    )


class FileSpec(BaseModel):
    """Селектор элемента-ссылки на скачиваемый файл.

    Имя файла берётся из текста элемента (или атрибута ``name_attribute``),
    URL скачивания с ЭТП — из атрибута ``url_attribute`` (по умолчанию href).
    """

    selector: str
    name_attribute: str | None = Field(
        default=None, description="атрибут с именем файла; None — текст элемента"
    )
    url_attribute: str = Field(default="href", description="атрибут с URL скачивания")


class DetailPageSpec(BaseModel):
    """Дополнительная страница деталей (например, позиции/лоты).

    Ссылка на неё находится на детальной странице по ``link_selector``; после
    перехода извлекаются ``variables``. Используется для полей, отсутствующих
    на основной детальной странице (например, ОКПД2 223-ФЗ на lot-list).
    """

    link_selector: str = Field(description="селектор ссылки на доп. страницу")
    variables: list[DomVariable] = Field(default_factory=list)


class DomDetailConfig(BaseModel):
    """Селекторы страницы детальной информации."""

    variables: list[DomVariable] = Field(default_factory=list)
    files: list[FileSpec] = Field(
        default_factory=list, description="элементы ссылок на скачиваемые файлы"
    )
    files_page: str | None = Field(
        default=None,
        description=(
            "имя html-файла страницы файлов (например, documents.html); если задано, "
            "файлы извлекаются с неё (URL = детальный URL с заменой имени html-файла)"
        ),
    )
    additional_pages: list[DetailPageSpec] = Field(
        default_factory=list,
        description="доп. страницы деталей (переход по ссылке с детальной страницы)",
    )


class FilterStep(BaseModel):
    """Один шаг DOM-манипуляции для установки/применения фильтра."""

    action: Literal["click", "fill", "select", "press", "wait", "set_checkbox"]
    selector: str
    value: str | None = None
    wait_ms: int = Field(default=500, ge=0)


class PurchaseFilter(BaseModel):
    """Описание одного фильтра."""

    name: str
    steps: list[FilterStep] = Field(description="DOM-шаги, приводящие к выбору значения")


class SortConfig(BaseModel):
    """Сортировка списка закупок.

    Порядок сортировки **фиксирован** — по дате публикации по убыванию
    (``publication_date_desc``): на нём основана стоп-логика по дате последней
    записи площадки (MAX(update_date) из БД).
    Другой порядок исключён (единственное допустимое значение в перечислении).
    Настраиваются только DOM-детали: где дропдаун и как называется нужный пункт.
    """

    order: Literal["publication_date_desc"] = "publication_date_desc"
    dropdown: str | None = Field(default=None, description="селектор выпадающего списка сортировки")
    option_text: str | None = Field(
        default=None, description="текст пункта «по дате публикации» (для клика в меню)"
    )


class CriteriaMapping(BaseModel):
    """Привязка одного ОБОБЩЁННОГО критерия поиска к конкретному запросу площадки.

    Критерии задаются в config_service.yaml (SearchCriteria) в бизнес-терминах;
    здесь каждый из них маппится на то, куда его подставить в запрос площадки:
      - ``json_path`` — точечный путь внутри ``filter_json`` (напр.
        ``needSpecificFilter.okpdPaths``, ``priceFrom``);
      - ``query_param`` — имя плоского query-параметра (напр. ``searchString``,
        ``priceFrom``, ``publishDateFrom``).
    Можно указать оба сразу (например, критерий попадает и в JSON, и в параметр).
    Ключ словаря criteria_map — один из известных критериев: ``publish_date``,
    ``okpd2``, ``nmck_min``, ``nmck_max``.
    """

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
    query_params: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "имя параметра запроса -> шаблон значения; плейсхолдеры {filter_json}, {state_json}"
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
            "обобщённый критерий (publish_date|okpd2|nmck_min|nmck_max) "
            "-> привязка к запросу площадки (JSON-путь и/или query-параметр)"
        ),
    )


class OrganizationConfig(BaseModel):
    """Извлечение ИНН заказчика — универсальный механизм (ADR-4).

    ``customer_link_selector`` — селектор ссылки на организацию (имя заказчика);
    href этой ссылки — URL страницы организации. ИНН получается:
      - прямо из href через ``inn_from_link_regex`` (ЕИС 223-ФЗ: ``inn=(\\d{10,12})``), или
      - переходом на страницу организации и извлечением по ``inn_page_selector``
        (mos.ru ``/companyProfile/customer/{id}``, ЕИС 44-ФЗ).
    Если настроен только ``customer_link_selector`` без способа получения ИНН — ИНН
    остаётся nullable (закупка сохраняется, ИНН дозаполняется позже).
    """

    customer_link_selector: str | None = Field(
        default=None, description="селектор ссылки на организацию (href = URL страницы организации)"
    )
    inn_from_link_regex: str | None = Field(
        default=None, description="regex извлечения ИНН из org-ссылки (например, inn=(\\d{10,12}))"
    )
    inn_from_org_page: bool = Field(
        default=False,
        description=(
            "открывать страницу организации для извлечения ИНН, если его нет в org-ссылке "
            "(по селектору inn_page_selector или обобщённым поиском 'ИНН <цифры>')"
        ),
    )
    inn_page_selector: str | None = Field(
        default=None, description="селектор ИНН на странице организации"
    )


class PlatformDom(BaseModel):
    """DOM-конфигурация одной площадки закупок.

    Содержит и селекторы извлечения (``list_config``/``detail``), и селекторы
    сортировки/фильтров (``sort``/``filters``) — всё, что связано с DOM площадки.
    ``search`` — URL-механизм фильтрации (приоритетен, если задан).
    """

    name: str
    url: str = Field(description="базовый адрес платформы")
    list_path: str = Field(default="", description="путь к странице списка закупок")
    list_config: DomListConfig
    detail: DomDetailConfig
    sort: SortConfig | None = Field(default=None, description="установка сортировки списка")
    filters: list[PurchaseFilter] = Field(
        default_factory=list, description="фильтры и порядок их DOM-шагов"
    )
    search: SearchFilterConfig | None = Field(
        default=None, description="URL-фильтр списка (приоритетнее DOM-шагов)"
    )
    organization: OrganizationConfig | None = Field(
        default=None, description="извлечение ИНН заказчика (ADR-4)"
    )


class DomConfig(BaseModel):
    """Конфигурация DOM-элементов по площадкам."""

    platforms: dict[str, PlatformDom] = Field(
        description="ключ platform_id -> конфигурация площадки"
    )

    @field_validator("platforms")
    @classmethod
    def _non_empty(cls, v: dict[str, PlatformDom]) -> dict[str, PlatformDom]:
        if not v:
            raise ValueError("config_dom должен содержать хотя бы одну площадку")
        return v
