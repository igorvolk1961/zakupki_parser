"""Работа со страницей списка закупок: вход, сортировка, фильтры, пагинация."""

from __future__ import annotations

import copy
import json
import logging
import urllib.parse
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.config.models import PlatformDom, SearchFilterConfig
from zakupki_parser.parser.filters import apply_filters

logger = logging.getLogger(__name__)

# Фиксированная пауза после загрузки страницы: networkidle на этой SPA не наступает.
SETTLE_MS = 3000

# Площадка работает в часовом поясе МСК (UTC+3) — даты фильтра в этом поясе.
MSK = timezone(timedelta(hours=3))


def _replace_placeholder(data: Any, placeholder: str, value: Any) -> None:
    """Рекурсивно заменяет строковые значения, равные ``placeholder``."""
    if isinstance(data, dict):
        for k, v in list(data.items()):
            if v == placeholder:
                data[k] = value
            else:
                _replace_placeholder(v, placeholder, value)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            if v == placeholder:
                data[i] = value
            else:
                _replace_placeholder(v, placeholder, value)


def build_query(search: SearchFilterConfig, cutoff: datetime | None) -> str:
    """Строит строку запроса по конфигурации ``search`` (маппинг только из конфига).

    Плейсхолдеры в ``query_params``: ``{filter_json}``, ``{state_json}`` и
    ``{publish_date_great_equal}``.
    """
    filter_json = copy.deepcopy(search.filter_json)
    date_str: str | None = None
    if cutoff is not None:
        date_str = cutoff.astimezone(MSK).strftime(search.date_great_equal_format)
        _replace_placeholder(filter_json, "{publish_date_great_equal}", date_str)

    filter_encoded = urllib.parse.quote(
        json.dumps(filter_json, ensure_ascii=False, separators=(",", ":"))
    )
    state_encoded = urllib.parse.quote(
        json.dumps(search.state_json, ensure_ascii=False, separators=(",", ":"))
    )

    parts: list[str] = []
    for key, template in search.query_params.items():
        value = template
        value = value.replace("{filter_json}", filter_encoded)
        value = value.replace("{state_json}", state_encoded)
        if date_str is not None:
            value = value.replace("{publish_date_great_equal}", urllib.parse.quote(date_str))
        parts.append(f"{key}={value}")
    return "&".join(parts)


def build_list_url(platform: PlatformDom, cutoff: datetime | None = None) -> str:
    """Возвращает URL страницы списка.

    Если у площадки задан ``search`` (URL-фильтр) и он включён — URL с фильтром;
    иначе — простой ``list_path`` (для площадок с DOM-фильтрами).
    """
    base = platform.url.rstrip("/") + platform.list_path
    search = platform.search
    if search is None or not search.enabled:
        return base
    query = build_query(search, cutoff)
    return f"{base}?{query}"


async def open_list_page(page: Page, platform: PlatformDom, cutoff: datetime | None = None) -> None:
    url = build_list_url(platform, cutoff)
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # networkidle на этой SPA не наступает (аналитика/чат), ждём фиксированно.
    await page.wait_for_timeout(SETTLE_MS)
    logger.info("Открыта страница списка: %s", page.url)


async def setup_sort_and_filters(
    page: Page, platform: PlatformDom, delayer: Delayer | None = None
) -> None:
    """Устанавливает сортировку и применяет фильтры из конфигурации площадки.

    Селекторы сортировки и шаги фильтров заданы в ``config_dom.yaml``
    (блоки ``platform.sort`` и ``platform.filters``).

    Порядок сортировки фиксирован (``publication_date_desc``) — на нём основана
    стоп-логика last_seen; конфиг-схема исключает другие значения.
    """
    sort = platform.sort
    if sort and sort.dropdown and sort.option_text:
        logger.info("Сортировка: %s (порядок фиксирован %s)", sort.option_text, sort.order)
        dropdown = page.locator(sort.dropdown)
        # SPA рендерит панель сортировки с задержкой — ждём появления.
        try:
            await dropdown.first.wait_for(state="visible", timeout=30000)
        except Exception:  # noqa: BLE001
            logger.warning("Дропдаун сортировки не найден: %s", sort.dropdown)
            return
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

    if platform.filters:
        await apply_filters(page, platform.filters)


def list_containers(page: Page, platform: PlatformDom) -> Locator:
    """Возвращает локатор контейнеров записей о закупках."""
    return page.locator(platform.list_config.container)


async def next_page_exists(page: Page, platform: PlatformDom) -> bool:
    """Есть ли переход на следующую страницу."""
    sel = platform.list_config.next_page
    if not sel:
        return False
    return await page.locator(sel).count() > 0


async def goto_next_page(page: Page, platform: PlatformDom, delayer: Delayer) -> bool:
    """Переходит на следующую страницу, возвращает True при успехе."""
    sel = platform.list_config.next_page
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
