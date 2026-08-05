"""Скачивание файлов заявки через элементы, указанные в ``config_dom.yaml``."""

from __future__ import annotations

import logging
import os
import re

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.storage.object_store import FileRef, ObjectStore

logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r'filename="?([^";]+)"?')
_FILENAME_STAR_RE = re.compile(r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)", re.IGNORECASE)


def _safe_filename(name: str) -> str:
    """Возвращает безопасное имя файла (только базовое имя, без путей).

    Имя приходит с сервера в Content-Disposition и может содержать ``../`` или
    разделители путей — не допускаем запись за пределы каталога хранилища.
    """
    base = os.path.basename(name.replace("\\", "/")).strip().strip('"')
    if base in ("", ".", ".."):
        return ""
    return base


def _filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    m = _FILENAME_STAR_RE.search(header) or _FILENAME_RE.search(header)
    if not m:
        return None
    return _safe_filename(m.group(1)) if m else None


def _matches_keywords(filename: str | None, keywords: list[str]) -> bool:
    """Проверяет, содержит ли имя файла хотя бы одно из ключевых слов."""
    if not filename:
        return False
    low = filename.lower()
    return any(k.lower() in low for k in keywords)


def split_technical_spec(
    files: list[dict[str, str]], keywords: list[str]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Разделяет файлы на техническое задание и остальные (по имени)."""
    ts = [f for f in files if _matches_keywords(f.get("name"), keywords)]
    others = [f for f in files if f not in ts]
    return ts, others


async def _file_name(page: Page, full: str) -> str | None:
    """Имя файла из Content-Disposition без скачивания тела (HEAD, затем GET)."""
    for method in ("head", "get"):
        try:
            resp = await getattr(page.request, method)(full, timeout=30000)
            try:
                fname = _filename_from_disposition(resp.headers.get("content-disposition"))
                if fname:
                    return fname
            finally:
                await resp.dispose()
        except Exception:  # noqa: BLE001
            continue
    return None


async def download_files(
    page: Page,
    platform: PlatformDom,
    store: ObjectStore,
    number: str,
    urls: list[str],
    only_keywords: list[str] | None = None,
) -> list[FileRef]:
    """Скачивает файлы заявки ``number`` в хранилище ``store``.

    ``urls`` — список URL скачивания файлов (с ЭТП). Скачивание идёт через
    ``page.request`` (APIRequestContext браузерного контекста) — он делит
    куки/сессию и UA с браузером и корректно обрабатывает ответ-файл.

    ``only_keywords`` — если задано, скачиваются только файлы, в имени которых
    есть хотя бы одно ключевое слово (например, только «техническое задание»).

    Возвращает ссылки на сохранённые файлы (``FileRef``).
    """
    if urls is None:
        raise ValueError("urls обязательны")
    refs: list[FileRef] = []
    for i, url in enumerate(urls, start=1):
        full = url if url.startswith("http") else platform.url.rstrip("/") + url

        if only_keywords:
            fname = await _file_name(page, full)
            if not _matches_keywords(fname, only_keywords):
                logger.info(
                    "Пропущен файл %s: имя не содержит ключевые слова %s",
                    fname or full,
                    only_keywords,
                )
                continue

        resp = await page.request.get(full, timeout=30000)
        try:
            if not resp.ok:
                logger.warning("Ошибка скачивания %s: HTTP %s", full, resp.status)
                continue
            content = await resp.body()
            fname = (
                _filename_from_disposition(resp.headers.get("content-disposition")) or f"file_{i}"
            )
            if not fname:
                logger.warning("Пустое имя файла из %s, пропуск", full)
                continue
            key = f"{number}/{fname}"
            ref = await store.put(key, content)
            refs.append(ref)
            logger.info("Сохранён файл заявки %s: %s", number, ref.url)
        finally:
            await resp.dispose()
    return refs
