"""Работа со страницей списка закупок: вход, сортировка, фильтры, пагинация."""

from __future__ import annotations

import copy
import json
import logging
import re
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


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def _resolve_okpd2_eis(codes: list[str], tree_file: str | None) -> dict[str, str] | None:
    """Резолвит коды ОКПД2 для ЕИС в параметр ``okpd2Ids``.

    Возвращает ``{"okpd2Ids": ...}`` — только собственные id выбранных кодов.
    Дочерние узлы подключаются флагом ``okpd2IdsWithNested=on`` (в статических
    query_params), поэтому перечислять всё поддерево не нужно. Для кода без
    собственного id берётся ближайший предок.
    """
    if not codes:
        return None
    if not tree_file:
        logger.warning("ОКПД2 коды заданы, но search-маппинг (ЕИС) не указан")
        return None
    try:
        tree = load_okpd_tree(tree_file)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить дерево ОКПД2 ЕИС %s: %s", tree_file, exc)
        return None

    code_to_id = tree.get("code_to_id") or {}
    ids: list[str] = []
    for code in codes:
        cid = code_to_id.get(code)
        if cid is None:
            cid = _nearest_ancestor_id(code, code_to_id)
        if cid and cid not in ids:
            ids.append(cid)
    if not ids:
        return None
    return {"okpd2Ids": ",".join(ids)}


def _nearest_ancestor_id(code: str, code_to_id: dict[str, str]) -> str | None:
    """id ближайшего предка кода (по цифровому префиксу) или None."""
    digits = _digits(code)
    best_len = 0
    best_id: str | None = None
    for c, cid in code_to_id.items():
        key_digits = _digits(c)
        if key_digits and digits.startswith(key_digits) and len(key_digits) > best_len:
            best_len = len(key_digits)
            best_id = cid
    return best_id


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
    if key == "update_date":
        # Дата «Обновлено» (ЕИС updateDateFrom) — тот же порог cutoff.
        if cutoff is None:
            return None
        return cutoff.astimezone(MSK).strftime(search.date_great_equal_format)
    if key == "deadline_from":
        # Срок подачи заявок не раньше сегодня (заявки с просроченным дедлайном
        # отсекаются сервером — дополняет stop_conditions.deadline_not_expired).
        return datetime.now(MSK).strftime(search.date_great_equal_format)
    if key == "okpd2":
        return _resolve_paths(criteria.okpd_codes, search.okpd_tree_file, "ОКПД2")
    if key == "fz44":
        return "on" if criteria.fz44 else None
    if key == "fz223":
        return "on" if criteria.fz223 else None
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
        if key == "okpd2" and mapping.query_params:
            values = _resolve_okpd2_eis(criteria.okpd_codes, search.okpd_tree_file)
            if values:
                for param, value in values.items():
                    extra_params[param] = value
            continue
        if key == "keywords":
            kws = criteria.keywords
            if not kws:
                continue
            # Слова склеиваются пробелом (одно значение поиска).
            joined = " ".join(kws)
            if mapping.json_path:
                # mos.ru: nameLike = {"value": "<слова>", "contains": true}.
                _set_json_path(filter_json, mapping.json_path, {"value": joined, "contains": True})
            if mapping.query_param:
                extra_params[mapping.query_param] = joined
            continue
        if key == "active_only":
            # Выбор «все/только активные»: только активные подставляет stateIdIn.
            if not criteria.active_only:
                continue
            ids = (search.state_ids or {}).get("active")
            if not ids:
                continue
            if mapping.json_path:
                _set_json_path(filter_json, mapping.json_path, ids)
            if mapping.query_param:
                extra_params[mapping.query_param] = _value_to_str(ids)
            continue
        value = _criteria_value(key, criteria, cutoff, search)
        if value is None or (isinstance(value, list) and not value):
            continue
        if mapping.json_path:
            _set_json_path(filter_json, mapping.json_path, value)
        if mapping.query_param:
            extra_params[mapping.query_param] = _value_to_str(value)

    filter_json_str = json.dumps(filter_json, ensure_ascii=False, separators=(",", ":"))
    state_json_str = json.dumps(state_json, ensure_ascii=False, separators=(",", ":"))

    parts: list[str] = []
    for key, template in search.query_params.items():
        value = template
        value = value.replace("{filter_json}", filter_json_str)
        value = value.replace("{state_json}", state_json_str)
        # Статические значения (в т.ч. кириллица/пробелы) URL-кодируются целиком.
        parts.append(f"{key}={urllib.parse.quote(value, safe='')}")
    for name, value in extra_params.items():
        parts.append(f"{name}={urllib.parse.quote(value, safe='')}")
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
