"""Модель подгрузки карточки закупки по явно заданному URL (US-5.5/FR-5.5)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from zakupki_parser.config.models.dom.variables import DomVariable


class DomByUrlRule(BaseModel):
    """Один вид URL детальной страницы площадки и способ сбора карточки с него.

    Обычный обход берёт поля уровня списка (номер, предмет, заказчик, НМЦК, даты)
    из карточки выдачи поиска, а детали — с детальной страницы/API. При добавлении
    по URL выдачи нет: ``url_pattern`` распознаёт URL детальной страницы (и
    отличает площадки с общим хостом — 44-ФЗ/223-ФЗ одного портала — или разные
    типы страниц одной площадки, напр. fabrikant), а поля уровня списка берутся с
    самой детальной страницы (``variables``) или из API площадки
    (``detail.api_format``).

    ``url_pattern`` — regex (``re.search``, без учёта регистра): именованная
    группа ``number`` — номер закупки, прочие именованные группы — поля запроса
    деталей через API (``detail_api``, напр. kind/platform_id у etpgpb,
    number/lot у tender_223).
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


class DomByUrlConfig(BaseModel):
    """Правила подгрузки закупки по URL детальной страницы (одна или несколько).

    Площадке с несколькими видами детальных страниц (fabrikant: 44-ФЗ на
    ``44.fabrikant.ru``, коммерческие на ``/v2/trades/procedure/...``) нужно
    несколько правил — разные ``url_pattern`` и ``variables``. Для остальных
    площадок правило одно: в YAML блок ``by_url`` может быть сразу правилом
    (``url_pattern``/``variables``/``defaults``), тогда оно оборачивается в
    список автоматически.
    """

    rules: list[DomByUrlRule] = Field(min_length=1, description="правила, в порядке приоритета")

    @model_validator(mode="before")
    @classmethod
    def _wrap_single_rule(cls, data: Any) -> Any:
        if isinstance(data, dict) and "rules" not in data and "url_pattern" in data:
            return {"rules": [data]}
        return data
