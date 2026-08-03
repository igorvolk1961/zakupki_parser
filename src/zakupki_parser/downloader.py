"""Скачивание файлов заявки через элементы, указанные в ``config_dom.yaml``."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail import detail_file_urls

logger = logging.getLogger(__name__)


async def download_files(
    page: Page,
    platform: PlatformDom,
    documents_dir: Path,
    number: str,
) -> list[Path]:
    """Скачивает файлы заявки ``number`` в ``documents_dir/number/``.

    Возвращает список сохранённых путей. Возвращается пустой список при отсутствии
    элементов файлов в конфиге.
    """
    target = documents_dir / number
    target.mkdir(parents=True, exist_ok=True)
    urls = await detail_file_urls(page, platform)
    saved: list[Path] = []
    for i, url in enumerate(urls, start=1):
        full = url if url.startswith("http") else platform.url.rstrip("/") + url
        async with page.expect_download(timeout=30000) as dl_info:
            await page.goto(full, wait_until="domcontentloaded")
        download = await dl_info.value
        suggested = download.suggested_filename or f"file_{i}"
        dest = target / suggested
        await download.save_as(str(dest))
        saved.append(dest)
        logger.info("Скачан файл: %s", dest)
    return saved
