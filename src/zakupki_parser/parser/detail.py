"""Работа со страницей детальной информации о закупке.

Единый интерфейс извлечения деталей ``extract_details`` (BR-08): для API-площадок
(``detail.api_format``) — ``fetch_api_details``, для DOM-площадок — переход на
детальную страницу и извлечение ``detail.variables``/файлов/ИНН. Вызывается
из обработчика ``POST /score`` ПОСЛЕ получения результата скоринга (одинаково
для всех площадок).
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api import fetch_api_details
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.organization import capture_customer_link, resolve_inn

logger = logging.getLogger(__name__)

# Пауза после клика по кнопке раскрытия списка документов — ждём отрисовку полного списка.
_FILES_EXPAND_WAIT_MS = 2000


def _absolute(base_url: str, href: str) -> str:
    """Возвращает абсолютный URL (href может быть абсолютным или относительным)."""
    if href.startswith("http"):
        return href
    return base_url.rstrip("/") + href


def files_page_url(detail_url: str, files_page: str) -> str:
    """URL страницы файлов: детальный URL с заменой имени html-файла.

    Например, ``.../view/common-info.html?regNumber=X`` + ``documents.html`` ->
    ``.../view/documents.html?regNumber=X``.
    """
    base, _, query = detail_url.partition("?")
    dir_part, _, _ = base.rpartition("/")
    result = f"{dir_part}/{files_page}"
    return f"{result}?{query}" if query else result


async def open_detail(page: Page, detail_url: str, platform: PlatformDom) -> None:
    """Переходит на детальную страницу закупки."""
    await page.goto(
        _absolute(platform.url, detail_url),
        wait_until="domcontentloaded",
        timeout=60000,
    )
    # networkidle на этой SPA не наступает, ждём фиксированно.
    await page.wait_for_timeout(3000)
    # Для async SPA ждём появления ключевого элемента (например, поля ОКПД2),
    # иначе данные могут ещё не отрисоваться клиентом.
    if platform.detail.wait_selector:
        locator = page.locator(platform.detail.wait_selector)
        try:
            await locator.first.wait_for(state="attached", timeout=30000)
            await page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не дождались появления %s на детальной странице %s",
                platform.detail.wait_selector,
                detail_url,
            )


async def extract_detail_vars(page: Page, platform: PlatformDom) -> dict[str, Any]:
    """Извлекает значения переменных со страницы детальной информации."""
    return await extract_from_scope(page, platform.detail.variables)


async def _expand_files(page: Page, platform: PlatformDom) -> None:
    """Раскрывает полный список документов, если есть кнопка «Смотреть все документы».

    На некоторых площадках (mos.ru) видимая часть списка файлов неполна: остальные
    ссылки (в т.ч. на ТЗ) появляются в DOM только после клика по кнопке раскрытия.
    Без этого шага файлы из скрытой части не попали бы в извлечённый список.
    Отсутствие кнопки или сбой клика не прерывают извлечение видимой части.
    """
    selector = platform.detail.files_expand
    if not selector:
        return
    button = page.locator(selector).first
    if await button.count() == 0:
        return
    try:
        await button.click()
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось раскрыть список документов (%s)", selector)
        return
    await page.wait_for_timeout(_FILES_EXPAND_WAIT_MS)


async def detail_files(page: Page, platform: PlatformDom) -> list[dict[str, str]]:
    """Возвращает список файлов закупки: ``{"name": ..., "url": ...}``.

    Имя — из текста элемента (или атрибута ``name_attribute``), URL скачивания
    с ЭТП — из атрибута ``url_attribute`` (по умолчанию ``href``). Перед извлечением
    раскрывает полный список документов (``detail.files_expand``), если задано.
    """
    await _expand_files(page, platform)
    result: list[dict[str, str]] = []
    for spec in platform.detail.files:
        locators = page.locator(spec.selector)
        count = await locators.count()
        for i in range(count):
            element = locators.nth(i)
            name = (
                await element.text_content()
                if not spec.name_attribute
                else await element.get_attribute(spec.name_attribute)
            )
            url = await element.get_attribute(spec.url_attribute)
            if not url:
                continue
            if url.startswith("/"):
                url = platform.url.rstrip("/") + url
            result.append({"name": (name or "").strip(), "url": url})
    return result


async def extract_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    detail_url: str | None,
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Единый интерфейс извлечения деталей закупки (BR-08).

    Одинаково для всех площадок:
    - API-площадки (``detail.api_format``) — ``fetch_api_details`` (JSON);
    - DOM-площадки — переход на детальную страницу + ``detail.variables``,
      доп. страницы, файловая страница, файлы и ИНН заказчика.

    Возвращает ``(detail_vars, files, inn)`` — те же поля, что у API-пути,
    чтобы обработчик POST /score не зависел от типа площадки.
    """
    if platform.detail.api_format:
        return await fetch_api_details(page, platform, list_vars, api_fields)

    if not detail_url:
        logger.debug("Детали: нет ссылки на детальную страницу")
        return {}, [], None
    await open_detail(page, detail_url, platform)
    detail_vars = await extract_detail_vars(page, platform)
    customer_link = await capture_customer_link(page, platform)
    # Доп. страницы деталей (например, ОКПД2 223-ФЗ на lot-list): переход по
    # ссылке с детальной страницы и извлечение дополнительных переменных.
    for spec in platform.detail.additional_pages:
        try:
            link = page.locator(spec.link_selector).first
            if await link.count() == 0:
                continue
            href = await link.get_attribute("href")
            if not href:
                continue
            page_url = href if href.startswith("http") else platform.url.rstrip("/") + href
            await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
            extra = await extract_from_scope(page, spec.variables)
            # Не затираем значение основной страницы, если на доп. странице поле
            # отсутствует (extract_from_scope вернул default=None).
            detail_vars.update({k: v for k, v in extra.items() if v is not None})
        except Exception as exc:  # noqa: BLE001
            logger.debug("Доп. страница деталей не обработана: %s", exc)
    # Файловая страница (например, ЕИС documents.html): URL = детальный URL
    # с заменой имени html-файла. У 223-ФЗ путь документов иной — переход может
    # не найтись, это не критично.
    files_page = platform.detail.files_page
    if files_page:
        try:
            await page.goto(
                files_page_url(detail_url, files_page),
                wait_until="domcontentloaded",
                timeout=45000,
            )
            await page.wait_for_timeout(3000)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Страница файлов не открылась (%s): %s", files_page, exc)
    files = await detail_files(page, platform)
    inn = await resolve_inn(page, platform, customer_link)
    return detail_vars, files, inn
