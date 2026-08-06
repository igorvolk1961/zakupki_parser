"""Загрузка demo-конфигов имитатора.

Рендер HTML строится по селекторам из ``demo_configs/config_dom.yaml`` — той самой
структуры, которую использует «Парсер закупок». Это гарантирует, что имитатор
воспроизводит именно ту разметку, которую ожидает парсер.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from zakupki_mos_simulator.settings import Settings


class DomSelectors:
    """Плоское представление селекторов площадки zakupki_mos из config_dom.yaml.

    Рендеры используют только селекторы сортировки и организации (ИНН); остальные
    селекторы/классы контракта зашиты в HTML-шаблоны как константы (см. web/dom_classes).
    """

    def __init__(self, data: dict[str, Any]) -> None:
        platform = data["platforms"]["zakupki_mos"]
        org = platform.get("organization") or {}
        self.sort_dropdown = (platform.get("sort") or {}).get("dropdown")
        self.sort_option_text = (platform.get("sort") or {}).get("option_text")
        self.organization = org


def load_dom_selectors(settings: Settings | None = None) -> DomSelectors:
    """Читает config_dom.yaml из demo-конфигов имитатора."""
    settings = settings or Settings()
    path = Path(settings.demo_configs_path) / "config_dom.yaml"
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return DomSelectors(data)
