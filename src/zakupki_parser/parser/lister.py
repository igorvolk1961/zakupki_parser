"""Работа со страницей списка закупок: вход, сортировка, фильтры, пагинация."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.config.models import FiltersConfig, PlatformDom
from zakupki_parser.parser.filters import apply_filters

logger = logging.getLogger(__name__)

# Фиксированная пауза после загрузки страницы: networkidle на этой SPA не наступает.
SETTLE_MS = 3000


async def open_list_page(page: Page, platform: PlatformDom) -> None:
    url = platform.url.rstrip("/") + platform.list_path
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # networkidle на этой SPA не наступает (аналитика/чат), ждём фиксированно.
    await page.wait_for_timeout(SETTLE_MS)
    logger.info("Открыта страница списка: %s", page.url)


async def setup_sort_and_filters(
    page: Page, platform: PlatformDom, filters_cfg: FiltersConfig
) -> None:
    """Устанавливает сортировку и применяет фильтры из ``config_filters.yaml``."""
    sort = filters_cfg.sort
    if sort.dropdown and sort.option_text:
        dropdown = page.locator(sort.dropdown)
        if await dropdown.count() > 0:
            await dropdown.first.click()
            await page.wait_for_timeout(400)
            option = dropdown.first.locator(f'.menu .item:has(.text:text-is("{sort.option_text}"))')
            if await option.count() > 0:
                await option.first.click()
                await page.wait_for_timeout(SETTLE_MS)
                logger.info("Сортировка установлена: %s", sort.option_text)
            else:
                await page.keyboard.press("Escape")
                logger.warning("Пункт сортировки '%s' не найден", sort.option_text)
        else:
            logger.warning("Дропдаун сортировки не найден: %s", sort.dropdown)

    await apply_filters(page, filters_cfg)


def list_containers(page: Page, platform: PlatformDom) -> Locator:
    """Возвращает локатор контейнеров записей о закупках."""
    return page.locator(platform.list.container)


async def next_page_exists(page: Page, platform: PlatformDom) -> bool:
    """Есть ли переход на следующую страницу."""
    sel = platform.list.next_page
    if not sel:
        return False
    return await page.locator(sel).count() > 0


async def goto_next_page(page: Page, platform: PlatformDom, delayer: Delayer) -> bool:
    """Переходит на следующую страницу, возвращает True при успехе."""
    sel = platform.list.next_page
    if not sel:
        return False
    locator = page.locator(sel)
    if await locator.count() == 0:
        return False
    await locator.first.click()
    await page.wait_for_timeout(SETTLE_MS)
    await delayer.sleep()
    return True


async def iter_container_records(
    page: Page, platform: PlatformDom, delayer: Delayer
) -> AsyncIterator[Locator]:
    """Итерируется по контейнерам записей на текущей странице."""
    containers = list_containers(page, platform)
    count = await containers.count()
    logger.info("Найдено контейнеров записей: %d", count)
    for i in range(count):
        await delayer.sleep()
        yield containers.nth(i)
