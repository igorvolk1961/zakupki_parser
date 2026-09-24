"""Модель подгрузки карточки закупки по явно заданному URL (US-5.5/FR-5.5)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from zakupki_parser.config.models.dom.variables import DomVariable


class DomByUrlConfig(BaseModel):
    """Подгрузка одной закупки по URL её детальной страницы (без прохода по списку).

    Обычный обход берёт поля уровня списка (номер, предмет, заказчик, НМЦК, даты)
    из карточки выдачи поиска, а детали — с детальной страницы/API. При добавлении
    по URL выдачи нет: ``url_pattern`` распознаёт URL детальной страницы площадки
    (и отличает площадки с общим хостом — 44-ФЗ/223-ФЗ одного портала), а поля
    уровня списка берутся с самой детальной страницы (``variables``) или из API
    площадки (``detail.api_format``).
    """

    url_pattern: str = Field(
        description=(
            "regex (re.search, без учёта регистра) URL детальной страницы. Именованная "
            "группа ``number`` — номер закупки; прочие именованные группы — поля запроса "
            "деталей через API (``detail_api``, напр. kind/platform_id у etpgpb)"
        ),
    )
    variables: list[DomVariable] = Field(
        default_factory=list,
        description=(
            "поля уровня списка (subject/customer/nmck/даты/статус/...), извлекаемые с "
            "детальной страницы — для DOM-площадок (у API-площадок их отдаёт API)"
        ),
    )
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "постоянные поля записи, не извлекаемые со страницы (напр. law площадки, "
            "где закон задан разделом портала, а не текстом карточки)"
        ),
    )

    @field_validator("url_pattern")
    @classmethod
    def _valid_regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"некорректный url_pattern: {exc}") from exc
        return v
