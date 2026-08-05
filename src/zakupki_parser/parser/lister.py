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
from zakupki_parser.config.models import PlatformDom, SearchCriteria, SearchFilterConfig
from zakupki_parser.okpd import load_okpd_tree, resolve_codes_to_paths
from zakupki_parser.parser.filters import apply_filters

logger = logging.getLogger(__name__)

# Фиксированная пауза после загрузки страницы: networkidle на этой SPA не наступает.
SETTLE_MS = 3000

# Площадка работает в часовом поясе МСК (UTC+3) — даты фильтра в этом поясе.
MSK = timezone(timedelta(hours=3))


def _resolve_paths(codes: list[str], tree_file: str | None, label: str) -> list[str] | None:
    """Резолвит коды (ОКПД2/регион) в пути через маппинг площадки.

    Возвращает None, если маппинг недоступен (коды не применятся).
    """
    if not codes:
        return []
    if not tree_file:
        logger.warning("%s коды заданы, но search-маппинг не указан", label)
        return None
    try:
        tree = load_okpd_tree(tree_file)
        return resolve_codes_to_paths(codes, tree, label=label)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить %s дерево %s: %s", label, tree_file, exc)
        return None


def _set_json_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Вставляет ``value`` в ``data`` по точечному пути ``path`` (создаёт словари)."""
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _value_to_str(value: Any) -> str:
    """Приводит значение критерия к строке для query-параметра."""
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    if isinstance(value, float):
        return str(int(value) if value.is_integer() else value)
    return str(value)


def _criteria_value(
    key: str,
    criteria: SearchCriteria,
    cutoff: datetime | None,
    search: SearchFilterConfig,
) -> Any:
    """Вычисляет значение обобщённого критерия по его ключу.

    Возвращает None (или пустой список), если критерий не задан — такой критерий
    пропускается и в запрос не попадает.
    """
    if key == "publish_date":
        if cutoff is None:
            return None
        return cutoff.astimezone(MSK).strftime(search.date_great_equal_format)
    if key == "okpd2":
        return _resolve_paths(criteria.okpd_codes, search.okpd_tree_file, "ОКПД2")
    if key == "region":
        return _resolve_paths(criteria.region_codes, search.region_tree_file, "региона")
    if key == "keywords":
        joined = " ".join(criteria.keywords).strip()
        return joined or None
    if key == "nmck_min":
        return criteria.nmck_min
    if key == "nmck_max":
        return criteria.nmck_max
    logger.warning("Неизвестный критерий поиска в criteria_map: %s", key)
    return None


def build_query(
    search: SearchFilterConfig,
    cutoff: datetime | None,
    criteria: SearchCriteria | None = None,
) -> str:
    """Строит строку запроса по конфигурации ``search``.

    Обобщённые критерии из ``config_service.yaml -> search_criteria`` подставляются
    в запрос через ``search.criteria_map`` (см. ``_criteria_value``): каждый критерий
    уходит либо в ``filter_json`` (по JSON-пути), либо в плоский query-параметр,
    либо в оба места. Не заданные критерии в запрос не попадают.
    """
    criteria = criteria or SearchCriteria()
    filter_json = copy.deepcopy(search.filter_json)
    state_json = copy.deepcopy(search.state_json)

    extra_params: dict[str, str] = {}
    for key, mapping in (search.criteria_map or {}).items():
        value = _criteria_value(key, criteria, cutoff, search)
        if value is None or (isinstance(value, list) and not value):
            continue
        if mapping.json_path:
            _set_json_path(filter_json, mapping.json_path, value)
        if mapping.query_param:
            extra_params[mapping.query_param] = _value_to_str(value)

    filter_encoded = urllib.parse.quote(
        json.dumps(filter_json, ensure_ascii=False, separators=(",", ":"))
    )
    state_encoded = urllib.parse.quote(
        json.dumps(state_json, ensure_ascii=False, separators=(",", ":"))
    )

    parts: list[str] = []
    for key, template in search.query_params.items():
        value = template
        value = value.replace("{filter_json}", filter_encoded)
        value = value.replace("{state_json}", state_encoded)
        parts.append(f"{key}={value}")
    for name, value in extra_params.items():
        parts.append(f"{name}={urllib.parse.quote(value)}")
    return "&".join(parts)


def build_list_url(
    platform: PlatformDom,
    cutoff: datetime | None = None,
    criteria: SearchCriteria | None = None,
) -> str:
    """Возвращает URL страницы списка.

    Если у площадки задан ``search`` (URL-фильтр) и он включён — URL с фильтром;
    иначе — простой ``list_path`` (для площадок с DOM-фильтрами).
    """
    base = platform.url.rstrip("/") + platform.list_path
    search = platform.search
    if search is None or not search.enabled:
        return base
    query = build_query(search, cutoff, criteria)
    return f"{base}?{query}"


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
