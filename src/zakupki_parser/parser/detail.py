"""Работа со страницей детальной информации о закупке."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.extractor import extract_from_scope

logger = logging.getLogger(__name__)


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


async def detail_files(page: Page, platform: PlatformDom) -> list[dict[str, str]]:
    """Возвращает список файлов закупки: ``{"name": ..., "url": ...}``.

    Имя — из текста элемента (или атрибута ``name_attribute``), URL скачивания
    с ЭТП — из атрибута ``url_attribute`` (по умолчанию ``href``).
    """
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
