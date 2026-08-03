"""Работа со страницей списка закупок: вход, сортировка, фильтры, пагинация."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.config.models import FiltersConfig, PlatformDom
from zakupki_parser.parser.filters import apply_filters
from zakupki_parser.parser.handlers import apply_handler

logger = logging.getLogger(__name__)


async def open_list_page(page: Page, platform: PlatformDom) -> None:
    url = platform.url.rstrip("/") + platform.list_path
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_load_state("networkidle")
    logger.info("Открыта страница списка: %s", page.url)


async def setup_sort_and_filters(
    page: Page, platform: PlatformDom, filters_cfg: FiltersConfig
) -> None:
    """Устанавливает сортировку по убыванию даты обновления и применяет фильтры."""
    # Сортировка по убыванию даты обновления (для площадки по умолчанию применяются
    # фильтры из config_filters; точный селектор сортировки уточняется в конфиге).
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
    await page.wait_for_load_state("networkidle")
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


async def extract_update_date(container: Locator, platform: PlatformDom) -> str | None:
    """Извлекает дату обновления записи (если селектор задан в конфиге)."""
    sel = platform.list.update_date
    if not sel:
        return None
    locator = container.locator(sel)
    if await locator.count() == 0:
        return None
    raw = await locator.first.inner_text()
    value = apply_handler("date_iso", raw)
    return str(value) if value is not None else None
