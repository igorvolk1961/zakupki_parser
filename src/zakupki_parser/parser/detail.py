"""Работа со страницей детальной информации о закупке."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.extractor import extract_from_scope

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
