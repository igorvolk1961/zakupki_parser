"""Навигация по странице списка закупок: вход, сортировка, пагинация.

Список открывается по ``build_list_url`` (см. ``query``), затем применяются
сортировка и DOM-фильтры. Пагинация — либо по query-параметру (``page_param``),
либо кликом по DOM-селектору (``next_page``).
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import AsyncIterator
from datetime import datetime

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.config.models import PlatformDom, SearchCriteria
from zakupki_parser.parser.filters import apply_filters
from zakupki_parser.parser.lister.query import build_list_url

logger = logging.getLogger(__name__)

# Фиксированная пауза после загрузки страницы: networkidle на этой SPA не наступает.
SETTLE_MS = 3000


async def open_list_page(
    page: Page,
    platform: PlatformDom,
    cutoff: datetime | None = None,
    criteria: SearchCriteria | None = None,
) -> None:
    url = build_list_url(platform, cutoff, criteria)
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # networkidle на этой SPA не наступает (аналитика/чат), ждём фиксированно.
    await page.wait_for_timeout(SETTLE_MS)
    # Логируем путь без query: полный filter-URL (URL-encoded JSON) слишком длинный.
    logger.info("Открыта страница списка: %s", page.url.split("?", 1)[0])


async def setup_sort_and_filters(
    page: Page, platform: PlatformDom, delayer: Delayer | None = None
) -> None:
    """Устанавливает сортировку и применяет фильтры из конфигурации площадки.

    Селекторы сортировки и шаги фильтров заданы в ``config_dom.yaml``
    (блоки ``platform.sort`` и ``platform.filters``).

    Обычный порядок сортировки фиксирован (``publication_date_desc``) — на нём
    основана стоп-логика по дате последней записи площадки. Если площадка
    сортирует по релевантности (``sort.by_relevance=true``), клик по дропдауну
    даты не выполняется — сортировку задаёт URL (sortField=relevance).
    """
    sort = platform.sort
    if sort and not sort.by_relevance and sort.dropdown and sort.option_text:
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
    elif sort and sort.by_relevance:
        logger.info("Сортировка по релевантности (задаётся URL sortField=relevance)")

    if platform.filters:
        await apply_filters(page, platform.filters)


def list_containers(page: Page, platform: PlatformDom) -> Locator:
    """Возвращает локатор контейнеров записей о закупках."""
    return page.locator(platform.list_config.container)


async def extract_total_results(page: Page, platform: PlatformDom) -> int | None:
    """Извлекает общее число результатов поиска из DOM первой страницы списка.

    Возвращает None, если селектор не задан или число не удалось распарсить.
    """
    lc = platform.list_config
    if not lc.total_results_selector:
        return None
    locator = page.locator(lc.total_results_selector)
    if await locator.count() == 0:
        logger.warning("Селектор общего числа результатов не найден: %s", lc.total_results_selector)
        return None
    text = (await locator.first.text_content() or "").strip()
    if not text:
        return None

    if lc.total_results_regex:
        m = re.search(lc.total_results_regex, text)
        if m is None:
            return None
        num = m.group(1) if m.lastindex else m.group(0)
    else:
        digits = re.findall(r"\d+", text)
        num = digits[0] if digits else None
    if num is None:
        return None
    try:
        return int(re.sub(r"\D", "", num))
    except ValueError:
        return None


def _increment_url_page(url: str, param: str) -> str:
    """Возвращает ``url`` с инкрементированным значением query-параметра ``param``.

    Параметр отсутствует — считается 1 (следующая страница = 2). Прочие параметры
    сохраняются в исходном виде (без перекодировки), переписывается только ``param``.
    """
    parts = urllib.parse.urlsplit(url)
    # Разделяем query на пары, сохраняя исходное (сырое) представление каждого
    # параметра, чтобы перекодировка через parse_qsl/urlencode не меняла их формат.
    raw_pairs: list[tuple[str, str]] = []
    for pair in parts.query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        raw_pairs.append((key, value))

    current = 1
    for key, value in raw_pairs:
        if key == param:
            try:
                current = int(value) if value else 1
            except ValueError:
                current = 1
            break

    next_param = f"{param}={current + 1}"
    existing = [f"{k}={v}" for k, v in raw_pairs if k != param]
    existing.append(next_param)
    query = "&".join(existing)
    return urllib.parse.urlunsplit(parts._replace(query=query))


async def next_page_exists(page: Page, platform: PlatformDom) -> bool:
    """Есть ли переход на следующую страницу.

    При URL-пагинации (``page_param`` задан) — есть, пока на текущей странице найдено
    не меньше ``page_size`` контейнеров (полная страница). Иначе — по DOM-селектору.
    """
    lc = platform.list_config
    if lc.page_param:
        if lc.page_size is None:
            # Без знания размера страницы считаем, что страница не последняя,
            # если на ней есть хоть один контейнер (стоп — по возврату 0).
            count = await list_containers(page, platform).count()
            return count > 0
        count = await list_containers(page, platform).count()
        return count >= lc.page_size
    sel = lc.next_page
    if not sel:
        return False
    return await page.locator(sel).count() > 0


async def goto_next_page(page: Page, platform: PlatformDom, delayer: Delayer) -> bool:
    """Переходит на следующую страницу, возвращает True при успехе.

    При URL-пагинации — инкрементирует ``page_param`` в текущем URL и выполняет
    навигацию; иначе — кликает по DOM-селектору ``next_page``.
    """
    lc = platform.list_config
    if lc.page_param:
        next_url = _increment_url_page(page.url, lc.page_param)
        await page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(SETTLE_MS)
        await delayer.sleep()
        return True
    sel = lc.next_page
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
