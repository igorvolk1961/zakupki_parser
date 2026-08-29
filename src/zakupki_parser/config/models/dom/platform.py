"""Модели платформы и корневой DOM-конфигурации (configs/dom/<platform_id>.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from zakupki_parser.config.models.dom.detail import DomDetailConfig
from zakupki_parser.config.models.dom.list import DomListConfig
from zakupki_parser.config.models.dom.search import SearchFilterConfig
from zakupki_parser.config.models.dom.variables import (
    OrganizationConfig,
    PurchaseFilter,
    SortConfig,
)


class PlatformDom(BaseModel):
    """DOM-конфигурация одной площадки закупок.

    Содержит и селекторы извлечения (``list_config``/``detail``), и селекторы
    сортировки/фильтров (``sort``/``filters``) — всё, что связано с DOM площадки.
    ``search`` — URL-механизм фильтрации (приоритетен, если задан).
    """

    name: str
    url: str = Field(description="базовый адрес платформы")
    domain_group: str | None = Field(
        default=None,
        description=(
            "общий бэкенд/домен для последовательного обхода: площадки с "
            "одинаковым ``domain_group`` (44-ФЗ и 223-ФЗ одного сайта) не "
            "обрабатываются параллельно (лимит ``max_concurrent_per_domain``). "
            "None — использовать hostname из ``url``"
        ),
    )
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
