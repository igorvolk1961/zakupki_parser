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
            "pub_date|deadline|law|regex"
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


class DomDetailConfig(BaseModel):
    """Селекторы страницы детальной информации."""

    variables: list[DomVariable] = Field(default_factory=list)
    files: list[DomVariable] = Field(
        default_factory=list, description="элементы ссылок на скачиваемые файлы"
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
    (``publication_date_desc``): на нём основана стоп-логика last_seen.
    Другой порядок исключён (единственное допустимое значение в перечислении).
    Настраиваются только DOM-детали: где дропдаун и как называется нужный пункт.
    """

    order: Literal["publication_date_desc"] = "publication_date_desc"
    dropdown: str | None = Field(default=None, description="селектор выпадающего списка сортировки")
    option_text: str | None = Field(
        default=None, description="текст пункта «по дате публикации» (для клика в меню)"
    )


class SearchFilterConfig(BaseModel):
    """URL-фильтр списка закупок — полностью конфигурируемый.

    Маппинг URL строится только из конфига: имена параметров запроса, структура
    JSON-параметров ``filter``/``state`` и формат даты порога. Плейсхолдеры:
      - ``{filter_json}`` — URL-encoded JSON из ``filter_json``;
      - ``{state_json}``  — URL-encoded JSON из ``state_json``;
      - ``{publish_date_great_equal}`` — дата порога (cutoff) в формате
        ``date_great_equal_format``.
    """

    enabled: bool = Field(default=True)
    query_params: dict[str, str] = Field(
        default_factory=dict,
        description="имя параметра запроса -> шаблон значения (с плейсхолдерами)",
    )
    filter_json: dict[str, Any] = Field(
        default_factory=dict, description="структура параметра filter (JSON)"
    )
    state_json: dict[str, Any] = Field(
        default_factory=dict, description="структура параметра state (JSON)"
    )
    date_great_equal_format: str = Field(
        default="%d.%m.%Y 00:00:00",
        description="формат даты порога для плейсхолдера publish_date_great_equal",
    )
    okpd_tree_file: str | None = Field(
        default=None,
        description="путь к маппингу ОКПД2 (код -> путь) для этой площадки",
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
