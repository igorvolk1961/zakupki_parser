"""Скачивание файлов заявки через элементы, указанные в ``config_dom.yaml``."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail import detail_file_urls

logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r'filename="?([^";]+)"?')


def _filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    m = _FILENAME_RE.search(header)
    return m.group(1).strip() if m else None


async def download_files(
    page: Page,
    platform: PlatformDom,
    documents_dir: Path,
    number: str,
    urls: list[str] | None = None,
) -> list[Path]:
    """Скачивает файлы заявки ``number`` в ``documents_dir/number/``.

    URL файлов либо передаются явно (``urls``), либо извлекаются из ``page``
    через ``config_dom.yaml -> detail.files``. Скачивание идёт через
    ``page.request`` (APIRequestContext браузерного контекста) — он делит
    куки/сессию и UA с браузером и корректно обрабатывает ответ-файл.

    Возвращает список сохранённых путей.
    """
    target = documents_dir / number
    target.mkdir(parents=True, exist_ok=True)
    if urls is None:
        urls = await detail_file_urls(page, platform)
    saved: list[Path] = []
    for i, url in enumerate(urls, start=1):
        full = url if url.startswith("http") else platform.url.rstrip("/") + url
        resp = await page.request.get(full, timeout=30000)
        try:
            if not resp.ok:
                logger.warning("Ошибка скачивания %s: HTTP %s", full, resp.status)
                continue
            content = await resp.body()
            fname = (
                _filename_from_disposition(resp.headers.get("content-disposition")) or f"file_{i}"
            )
            dest = target / fname
            dest.write_bytes(content)
            saved.append(dest)
            logger.info("Скачан файл: %s (%d байт)", dest, len(content))
        finally:
            await resp.dispose()
    return saved
