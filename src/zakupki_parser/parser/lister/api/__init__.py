"""API-листер: получение списка закупок через JSON API площадки вместо DOM.

Площадки типа etpgpb рендерят на SSR-странице списка базовую выдачу, а реальную
фильтрацию (search/okpd/стадия) выполняет только внутренний API, который дергает
SPA после гидрации. Парсинг DOM такой страницы хрупок (гонка SSR/SPA), поэтому
для таких площадок список читается напрямую из API (``search.api_endpoint``):
query строится так же (``query_params`` + ``criteria_map``), ответ разбирается в
карточку записи (поля уровня списка).

Реализация разбита на подпакеты: ``http`` (запрос/разбор JSON), ``url``
(построение URL), ``fetch`` (выборка записей), ``parse`` (по-платформенные
парсеры item'ов). Здесь — реэкспорт для совместимости с прежним модулем
``lister/api.py``.
"""

from __future__ import annotations

from zakupki_parser.parser.lister.api.fetch import fetch_api_items
from zakupki_parser.parser.lister.api.http import request_json
from zakupki_parser.parser.lister.api.parse import MSK, parse_api_item
from zakupki_parser.parser.lister.api.url import build_api_list_url

__all__ = [
    "MSK",
    "build_api_list_url",
    "fetch_api_items",
    "parse_api_item",
    "request_json",
]
