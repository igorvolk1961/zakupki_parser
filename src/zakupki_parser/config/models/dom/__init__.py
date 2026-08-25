"""Модели DOM-конфигурации площадки и фильтров (configs/dom/<platform_id>.yaml).

Модели разбиты по поддоменам (подпакеты): ``variables`` (переменные/файлы/
фильтры/сортировка), ``list`` (страница списка), ``detail`` (страница деталей),
``search`` (URL-фильтр и маппинг критериев), ``platform`` (PlatformDom/DomConfig).
Здесь — реэкспорт для совместимости с прежним модулем ``config/models/dom.py``.
"""

from __future__ import annotations

from zakupki_parser.config.models.dom.detail import DomDetailConfig
from zakupki_parser.config.models.dom.list import DomListConfig
from zakupki_parser.config.models.dom.platform import DomConfig, PlatformDom
from zakupki_parser.config.models.dom.search import (
    CriteriaMapping,
    FilterMapping,
    SearchFilterConfig,
)
from zakupki_parser.config.models.dom.variables import (
    DetailPageSpec,
    DomVariable,
    FileSpec,
    FilterStep,
    OrganizationConfig,
    PurchaseFilter,
    SortConfig,
)

__all__ = [
    "CriteriaMapping",
    "DetailPageSpec",
    "DomConfig",
    "DomDetailConfig",
    "DomListConfig",
    "DomVariable",
    "FileSpec",
    "FilterMapping",
    "FilterStep",
    "OrganizationConfig",
    "PlatformDom",
    "PurchaseFilter",
    "SearchFilterConfig",
    "SortConfig",
]
