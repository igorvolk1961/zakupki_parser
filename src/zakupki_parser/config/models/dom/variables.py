"""Базовые модели DOM-конфигурации: переменные, файлы, фильтры, сортировка."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
            "опциональная постобработка: none|strip|float|int|date_iso|datetime|regex_datetime|"
            "ru_date|lower|pub_date|deadline|law|regex|money|dates|security"
        ),
    )
    handler_arg: str | None = Field(
        default=None, description="аргумент для обработчика (например, regex-паттерн)"
    )
    multiple: bool = Field(
        default=False,
        description=(
            "собрать значение из ВСЕХ элементов, совпавших с селектором, склеив их "
            "через ``separator`` (вместо одного элемента по ``index``/первого). "
            "Нужно для полей с несколькими значениями (например, несколько кодов ОКПД2)"
        ),
    )
    separator: str = Field(
        default=", ",
        description="разделитель для значений, когда ``multiple=true``",
    )
    default: Any = None


class FileSpec(BaseModel):
    """Селектор элемента-ссылки на скачиваемый файл.

    Имя файла берётся из текста элемента (или атрибута ``name_attribute``),
    URL скачивания с ЭТП — из атрибута ``url_attribute`` (по умолчанию href).

    Для разметки, где имя и URL лежат в РАЗНЫХ элементах (fabrikant: имя в
    ``td.procedure-document-file span``, URL — в соседней ячейке ``a.download``),
    задаются относительные ``name_selector``/``url_selector`` (локаторы внутри
    элемента, найденного по ``selector``).
    """

    selector: str
    name_attribute: str | None = Field(
        default=None, description="атрибут с именем файла; None — текст элемента"
    )
    url_attribute: str = Field(default="href", description="атрибут с URL скачивания")
    name_selector: str | None = Field(
        default=None,
        description=(
            "относительный селектор элемента с именем файла ВНУТРИ элемента, "
            "найденного по ``selector``; если задан, приоритетнее ``name_attribute``"
        ),
    )
    url_selector: str | None = Field(
        default=None,
        description=(
            "относительный селектор элемента с URL скачивания ВНУТРИ элемента, "
            "найденного по ``selector``; None — URL берётся из самого элемента"
        ),
    )


class DetailPageSpec(BaseModel):
    """Дополнительная страница деталей (например, позиции/лоты).

    Ссылка на неё находится на детальной странице по ``link_selector``; после
    перехода извлекаются ``variables``. Используется для полей, отсутствующих
    на основной детальной странице (например, ОКПД2 223-ФЗ на lot-list).
    """

    link_selector: str = Field(description="селектор ссылки на доп. страницу")
    variables: list[DomVariable] = Field(default_factory=list)


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

    Обычный порядок **фиксирован** — по дате публикации по убыванию
    (``publication_date_desc``): на нём основана стоп-логика по дате последней
    записи площадки (MAX(update_date) из БД).
    Другой порядок исключён (единственное допустимое значение в перечислении).

    Если площадка поддерживает сортировку по релевантности (``by_relevance=true``),
    сортируем по релевантности и НЕ отсекаем по дате (стоп-логика не применяется):
    обход идёт до конца пагинации.
    """

    order: Literal["publication_date_desc"] = "publication_date_desc"
    by_relevance: bool = Field(
        default=False,
        description=(
            "сортировать по релевантности вместо даты публикации; при true "
            "стоп-логика по дате не применяется (обходим все страницы)"
        ),
    )
    dropdown: str | None = Field(default=None, description="селектор выпадающего списка сортировки")
    option_text: str | None = Field(
        default=None, description="текст пункта «по дате публикации» (для клика в меню)"
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
