"""Движок применения фильтров по конфигурации площадки.

Шаги фильтров заданы в ``config_dom.yaml`` (блок ``platform.filters``).
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from zakupki_parser.config.models import PurchaseFilter

logger = logging.getLogger(__name__)


async def apply_filters(page: Page, filters: list[PurchaseFilter]) -> None:
    """Последовательно выполняет DOM-шаги всех фильтров площадки."""
    for purchase_filter in filters:
        logger.info("Применяем фильтр '%s'", purchase_filter.name)
        for step in purchase_filter.steps:
            if step.action == "wait":
                await page.wait_for_timeout(step.wait_ms)
                continue
            locator = page.locator(step.selector)
            if step.action == "click":
                await locator.first.click()
            elif step.action == "fill" and step.value is not None:
                await locator.first.fill(step.value)
            elif step.action == "press" and step.value is not None:
                await locator.first.press(step.value)
            elif step.action == "set_checkbox":
                # Строковые значения «false»/«0» не должны приводиться к True.
                checked = str(step.value).strip().lower() not in ("false", "0", "")
                await locator.first.set_checked(checked)
            if step.wait_ms:
                await page.wait_for_timeout(step.wait_ms)
