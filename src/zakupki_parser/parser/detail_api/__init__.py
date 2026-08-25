"""Извлечение деталей закупки через открытые API площадок (вместо DOM).

Площадки с ``detail.api_format`` (lot_online, etpgpb, ...) отдают поля деталей
(ОКПД2, позиции, заказчик с ИНН, НМЦК, файлы) по JSON API без открытия
браузерной страницы. Функции здесь возвращают ``(detail_vars, files, inn)``:
  - ``detail_vars`` — dict имён переменных (те же, что извлекались бы из DOM),
  - ``files`` — список ``{"name": ..., "url": ...}``,
  - ``inn`` — ИНН заказчика из API (None — резолвить как раньше через DOM).

Реализация по платформам вынесена в подпакеты (``lot_online``, ``etpgpb``,
``mos``, ``tender_223``); общие HTTP-хелперы — в ``http``. Здесь — реестр
платформ и публичный вход ``fetch_api_details`` (совместимость с прежним
модулем ``parser/detail_api.py``).
"""

from __future__ import annotations

from typing import Any, cast

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.etpgpb import _etpgpb_details
from zakupki_parser.parser.detail_api.lot_online import _lot_online_details
from zakupki_parser.parser.detail_api.mos import _mos_details
from zakupki_parser.parser.detail_api.tender_223 import _tender_223_details

_API_DETAILS: dict[str, Any] = {
    "lot_online": _lot_online_details,
    "etpgpb": _etpgpb_details,
    "mos": _mos_details,
    "tender_223": _tender_223_details,
}


async def fetch_api_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Извлекает детали через API площадки (по ``platform.detail.api_format``)."""
    fmt = platform.detail.api_format
    handler = _API_DETAILS.get(fmt or "")
    if handler is None:
        raise RuntimeError(f"Неизвестный api_format деталей: {fmt}")
    return cast(
        tuple[dict[str, Any], list[dict[str, str]], str | None],
        await handler(page, platform, list_vars, api_fields),
    )


__all__ = ["fetch_api_details", "_API_DETAILS"]
