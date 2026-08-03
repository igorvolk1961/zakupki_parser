"""Движок применения фильтров и сортировки по ``config_filters.yaml``."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from zakupki_parser.config.models import FiltersConfig

logger = logging.getLogger(__name__)

_ACTION_MAP = {
    "click": "click",
    "fill": "fill",
    "press": "press",
    "set_checkbox": "set_checked",
    "wait": None,
}


async def apply_filters(page: Page, cfg: FiltersConfig) -> None:
    """Последовательно выполняет шаги всех фильтров, затем применяет их."""
    for purchase_filter in cfg.filters:
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
                await locator.first.set_checked(bool(step.value))
            if step.wait_ms:
                await page.wait_for_timeout(step.wait_ms)

    if cfg.apply_button:
        await page.locator(cfg.apply_button).first.click()
        logger.info("Нажали кнопку применения фильтров")
    if cfg.wait_ms_after_apply:
        await page.wait_for_timeout(cfg.wait_ms_after_apply)
