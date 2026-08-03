"""Извлечение значений переменных из DOM по ``config_dom.yaml``.

Извлечение выполняется в контексте scoped-локатора (страница или контейнер записи).
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Locator, Page

from zakupki_parser.config.models import DomVariable
from zakupki_parser.parser.handlers import apply_handler

logger = logging.getLogger(__name__)


async def _element_value(locator: Locator, var: DomVariable) -> Any:
    if var.attribute:
        return await locator.get_attribute(var.attribute)
    return await locator.inner_text()


async def extract_from_scope(scope: Page | Locator, variables: list[DomVariable]) -> dict[str, Any]:
    """Извлекает значения ``variables`` в контексте ``scope``.

    Для каждой переменной выбирается первый подходящий элемент (или ``default``).
    """
    result: dict[str, Any] = {}
    for var in variables:
        locators = scope.locator(var.selector)
        count = await locators.count()
        value: Any = var.default
        if count > 0:
            try:
                raw = await _element_value(locators.first, var)
                value = apply_handler(var.handler, raw)
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось извлечь '%s' (%s)", var.name, var.selector)
                value = var.default
        result[var.name] = value
    return result
