"""API-листер: получение списка закупок через JSON API площадки вместо DOM.

Площадки типа etpgpb рендерят на SSR-странице списка базовую выдачу, а реальную
фильтрацию (search/okpd/стадия) выполняет только внутренний API, который дергает
SPA после гидрации. Парсинг DOM такой страницы хрупок (гонка SSR/SPA), поэтому
для таких площадок список читается напрямую из API (``search.api_endpoint``):
query строится так же (``query_params`` + ``criteria_map``), ответ разбирается в
карточку записи (поля уровня списка).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom, SearchCriteria
from zakupki_parser.parser.lister.query import build_query

logger = logging.getLogger(__name__)


def build_api_list_url(platform: PlatformDom, criteria: SearchCriteria | None = None) -> str:
    """Строит URL GET-запроса к API списка (``platform.search.api_endpoint``).

    Если API-эндпоинт не задан или поиск выключен — возвращается обычный
    ``list_path`` (DOM-листер).
    """
    search = platform.search
    if search is None or not search.enabled or not search.api_endpoint:
        return platform.url.rstrip("/") + platform.list_path
    base = platform.url.rstrip("/") + search.api_endpoint
    query = build_query(search, None, criteria)
    return f"{base}?{query}"


async def fetch_api_items(page: Page, url: str) -> list[dict[str, Any]]:
    """GET-запрос к API списка через браузер (page.request, общий контекст с SPA).

    Возвращает список ``data`` из JSON-ответа. Сбой сети/HTTP/структуры поднимает
    исключение — вызывающий ретраит через ``run_with_retry``.
    """
    resp = await page.request.get(url, timeout=60000)
    if not resp.ok:
        raise RuntimeError(f"API списка вернул HTTP {resp.status}")
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"API списка: некорректный JSON: {exc}") from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("API списка: отсутствует поле data (list)")
    return items


def _iso_dt(value: Any) -> datetime | None:
    """ISO-дата из API (например '2026-08-17T16:48:00.000+03:00') -> aware datetime."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _amount(value: Any) -> float | None:
    """Денежное значение из API ('0.0', '6 459 964,61') -> float (None при пустом)."""
    if value is None:
        return None
    try:
        return float(re.sub(r"[^\d.,\-]", "", str(value)).replace(",", "."))
    except ValueError:
        return None


def parse_api_item(item: dict[str, Any]) -> dict[str, Any]:
    """Маппит один item JSON API списка в dict переменных карточки (list.variables).

    Поля соответствуют именам ``list_config.variables``, чтобы дальше записи шли
    по общему пути (детальная страница, stop-условия, скоринг, сохранение).
    """
    attrs = item.get("attributes") or {}
    list_vars: dict[str, Any] = {}
    reg = attrs.get("registry_number")
    list_vars["number"] = re.sub(r"^\s*№\s*", "", str(reg)) if reg is not None else ""
    list_vars["subject"] = attrs.get("title")
    list_vars["nmck"] = _amount(attrs.get("amount"))
    list_vars["publication_date"] = _iso_dt(attrs.get("date_published"))
    list_vars["update_date"] = _iso_dt(attrs.get("date_last_update"))
    list_vars["deadline"] = _iso_dt(attrs.get("end_registration"))
    list_vars["customer"] = attrs.get("company_name")
    list_vars["status"] = attrs.get("stage") or ""
    kind = str(attrs.get("kind") or "").lower()
    if "44" in kind:
        list_vars["law"] = "44-ФЗ"
    elif "223" in kind:
        list_vars["law"] = "223-ФЗ"
    list_vars["purchase_type"] = attrs.get("custom_procedure_type_name") or attrs.get(
        "procedure_type_name"
    )
    # Путь детальной страницы: новый «ребрендинг»-путь (как в карточках), иначе старый.
    list_vars["detail_path"] = attrs.get("rebranding_truncated_path") or attrs.get("truncated_path")
    return list_vars
