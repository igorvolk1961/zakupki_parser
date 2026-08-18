"""Модели DOM-конфигурации площадки и фильтров (configs/dom/<platform_id>.yaml)."""

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
    page_param: str | None = Field(
        default=None,
        description=(
            "имя query-параметра страницы для URL-пагинации (напр. page); если задан, "
            "переходы между страницами выполняются изменением параметра в URL, а не кликом "
            "по селектору next_page (для площадок с серверной постраничной выдачей)"
        ),
    )
    page_size: int | None = Field(
        default=None,
        ge=1,
        description=(
            "число записей на страницу — условие останова при URL-пагинации: следующая "
            "страница есть, пока на текущей найдено >= page_size контейнеров"
        ),
    )
    total_results_selector: str | None = Field(
        default=None,
        description=(
            "CSS-селектор элемента с общим числом результатов поиска (на первой странице "
            "списка). Используется для раннего пропуска прохода, если все результаты уже "
            "сохранены в БД. None — не извлекать."
        ),
    )
    total_results_regex: str | None = Field(
        default=None,
        description=(
            "regex для извлечения числа из текста total_results_selector (например "
            "'Найдено: (\\d+)'). Если не задан, из текста берутся все цифры подряд. "
            "Группа (если есть) — число результатов, иначе первое совпадение."
        ),
    )
    active_statuses: list[str] | None = Field(
        default=None,
        description=(
            "статусы закупки, считающиеся активными. Закупка не активна (is_active=false), "
            "если её переменная status не входит в список (завершённые, отменённые и т.п.). "
            "None — активность не определяется, is_active=true."
        ),
    )
    number_from_url_regex: str | None = Field(
        default=None,
        description=(
            "запасной источник номера закупки: regex, извлекающий номер из URL детальной "
            "страницы (первая группа или всё совпадение). Применяется, когда номер не "
            "извлёкся из карточки списка (селектор/паттерн не совпали), иначе запись "
            "отбрасывается в repository.upsert как не имеющая номера. Например, для "
            "roseltorg '/procedure/([^/]+)' из /procedure/COM14082600147/1. None — не применять."
        ),
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
    wait_selector: str | None = Field(
        default=None,
        description=(
            "селектор элемента, появления которого дождаться на детальной странице "
            "перед извлечением переменных (для async SPA, где данные грузятся "
            "клиентом после загрузки оболочки)"
        ),
    )
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
    files_expand: str | None = Field(
        default=None,
        description=(
            "CSS-селектор кнопки раскрытия полного списка документов "
            "(например «Смотреть все документы»). Если задан — кликаем по ней перед "
            "извлечением файлов, чтобы в DOM попали все ссылки на файлы (в т.ч. из "
            "скрытой/лениво отрисованной части списка)"
        ),
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
    ``update_date``, ``deadline_from``, ``okpd2``, ``nmck_min``, ``nmck_max``,
    ``keywords``.
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
    raw_json: str | None = Field(
        default=None,
        description=(
            "имя query-параметра, значение которого — JSON-массив объектов "
            '`[{"key": <код>}]` по кодам критерия okpd2. Используется для lot-online 223 '
            '(okpd2=[{"key":"62.02"}], названия не нужны)'
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
    query_params: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description=(
            "имя параметра запроса -> шаблон значения; плейсхолдеры {filter_json}, {state_json}. "
            "Значение может быть списком — тогда параметр повторяется (status[]=2&status[]=3)"
        ),
    )
    keywords_sort: str | None = Field(
        default=None,
        description=(
            "значение параметра sort, подставляемое вместо статического (query_params['sort']), "
            "когда в критериях есть ключевые слова. Нужно площадкам, чей текстовый поиск "
            "работает только с сортировкой по релевантности (etpgpb: search фильтрует выдачу "
            "только с sort=by_relevance; с by_published_desc API возвращает нерелевантные записи)"
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
            "nmck_min|nmck_max|keywords|active_only) -> привязка к "
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
    keywords_one_at_a_time: bool = Field(
        default=True,
        description=(
            "как слова между собой сочетаются на площадке (AND/OR — зависит от площадки), "
            "но нам всегда нужен OR. Если true (по умолчанию) — каждое слово из "
            "search_criteria.keywords перебирается отдельным поиском, результаты "
            "объединяются с дедупом по номеру закупки. False — все слова склеиваются "
            "пробелом в одно значение (AND на площадке)."
        ),
    )
    min_keyword_len: int | None = Field(
        default=None,
        ge=1,
        description=(
            "минимальная длина ключевого слова для поиска площадки. Площадки, чей "
            "поисковый движок игнорирует короткие запросы (например fabrikant: слова "
            "короче 3 символов не фильтруют и возвращают весь список закупок), задают "
            "этот порог — слова из search_criteria.keywords короче него отбрасываются, "
            "чтобы не открывать заведомо всеобъемлющий поиск."
        ),
    )
    keywords_separator: str = Field(
        default=" ",
        description=(
            "как склеить несколько слов search_criteria.keywords в одно значение поиска "
            "(используется при keywords_one_at_a_time=false). Для площадок с союзом «или» "
            "(lot-online, b2b-center) — ' или ', чтобы слова искались по «ИЛИ» в одном "
            "запросе, а не по-одному"
        ),
    )
    keywords_quote_phrases: bool = Field(
        default=False,
        description=(
            "заключать многословные ключевые фразы в двойные кавычки (например, "
            "'\"искусственный интеллект\" или автоматизация'). Нужно для площадок, где "
            "фраза из нескольких слов должна совпадать точно (lot-online)"
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
