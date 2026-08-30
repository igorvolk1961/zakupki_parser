"""Модель селекторов страницы списка закупок."""

from __future__ import annotations

from pydantic import BaseModel, Field

from zakupki_parser.config.models.dom.variables import DomVariable


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
