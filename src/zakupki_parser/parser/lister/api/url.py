"""Построение URL GET-запроса к API списка закупок."""

from __future__ import annotations

from zakupki_parser.config.models import PlatformDom, SearchCriteria
from zakupki_parser.parser.lister.query import build_query


def build_api_list_url(
    platform: PlatformDom,
    criteria: SearchCriteria | None = None,
    offset: int = 0,
) -> str:
    """Строит URL GET-запроса к API списка (``platform.search.api_endpoint``).

    Если API-эндпоинт не задан или поиск выключен — возвращается обычный
    ``list_path`` (DOM-листер). ``offset`` — плейсхолдер ``{offset}`` в шаблонах
    query_params (пагинация take/skip, например mos.ru). Эндпоинт может быть
    абсолютным (API на другом хосте, например old.zakupki.mos.ru).
    """
    search = platform.search
    if search is None or not search.enabled or not search.api_endpoint:
        return platform.url.rstrip("/") + platform.list_path
    if search.api_endpoint.startswith("http"):
        base = search.api_endpoint
    else:
        base = platform.url.rstrip("/") + search.api_endpoint
    query = build_query(search, None, criteria, offset=offset)
    return f"{base}?{query}"
