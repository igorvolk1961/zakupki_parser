"""Работа со страницей детальной информации о закупке."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.extractor import extract_from_scope

logger = logging.getLogger(__name__)


async def open_detail(page: Page, detail_url: str, platform: PlatformDom) -> None:
    """Переходит на детальную страницу закупки."""
    url = platform.url.rstrip("/") + detail_url
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # networkidle на этой SPA не наступает, ждём фиксированно.
    await page.wait_for_timeout(3000)


async def extract_detail_vars(page: Page, platform: PlatformDom) -> dict[str, Any]:
    """Извлекает значения переменных со страницы детальной информации."""
    return await extract_from_scope(page, platform.detail.variables)


async def detail_file_urls(page: Page, platform: PlatformDom) -> list[str]:
    """Возвращает абсолютные URL скачиваемых файлов закупки."""
    result: list[str] = []
    for var in platform.detail.files:
        locators = page.locator(var.selector)
        count = await locators.count()
        for i in range(count):
            href = await locators.nth(i).get_attribute("href")
            if not href:
                continue
            result.append(href)
    return result
