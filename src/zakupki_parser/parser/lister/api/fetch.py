"""Получение и разбор ответа API списка (выборка записей по пути в JSON)."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from zakupki_parser.parser.lister.api.http import request_json


async def fetch_api_items(
    page: Page, url: str, items_key: str | None = None
) -> list[dict[str, Any]]:
    """GET-запрос к API списка через браузер (page.request, общий контекст с SPA).

    Возвращает список записей: без ``items_key`` — ``data`` (etpgpb), иначе —
    ``data[items_key]`` (например, ``data.items`` реестра lot-online). Сбой
    сети/HTTP/структуры поднимает исключение — вызывающий ретраит через
    ``run_with_retry``.
    """
    data = await request_json(page, "GET", url, label="API списка")
    # Расположение списка в ответе: etpgpb — data (list); lot-online — data.items;
    # tender indexer — data (list на верхнем уровне); mos Query API — items на
    # верхнем уровне (без обёртки data). items_key задаёт вложенный ключ.
    raw = data.get("data") if isinstance(data, dict) else None
    if items_key:
        if isinstance(raw, dict):
            items = raw.get(items_key)
        else:
            items = data.get(items_key) if isinstance(data, dict) else None
    else:
        items = raw
    if not isinstance(items, list):
        path = f"data.{items_key}" if items_key else "data"
        raise RuntimeError(f"API списка: отсутствует поле {path} (list)")
    return items
