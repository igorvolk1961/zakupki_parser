"""Модель селекторов страницы детальной информации."""

from __future__ import annotations

from pydantic import BaseModel, Field

from zakupki_parser.config.models.dom.variables import DetailPageSpec, DomVariable, FileSpec


class DomDetailConfig(BaseModel):
    """Селекторы страницы детальной информации."""

    variables: list[DomVariable] = Field(default_factory=list)
    api_format: str | None = Field(
        default=None,
        description=(
            "формат извлечения деталей через открытый API площадки вместо DOM: "
            "lot_online | etpgpb | mos | tender_223. None — детали извлекаются из DOM-страницы. "
            "Парсер получает поля из JSON-ответа API (okpd2/заказчик/ИНН/файлы), "
            "детальная страница не открывается"
        ),
    )
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
    files_page_link: str | None = Field(
        default=None,
        description=(
            "CSS-селектор ссылки на страницу файлов (вкладка «Документация» и т.п.); "
            "переход выполняется после извлечения переменных, перед извлечением файлов. "
            "Отличается от ``files_page`` тем, что URL берётся из href ссылки на "
            "детальной странице, а не выводится заменой имени html-файла"
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
