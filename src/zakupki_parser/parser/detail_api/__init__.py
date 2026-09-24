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
from zakupki_parser.parser.detail_api.etpgpb import _etpgpb_by_url, _etpgpb_details
from zakupki_parser.parser.detail_api.lot_online import _lot_online_by_url, _lot_online_details
from zakupki_parser.parser.detail_api.mos import _mos_by_url, _mos_details
from zakupki_parser.parser.detail_api.tender_223 import _tender_223_by_url, _tender_223_details

_API_DETAILS: dict[str, Any] = {
    "lot_online": _lot_online_details,
    "etpgpb": _etpgpb_details,
    "mos": _mos_details,
    "tender_223": _tender_223_details,
}

# Подгрузка закупки по URL детальной страницы (US-5.5): поля уровня списка И
# детали одним проходом. Площадки без записи здесь — по URL не подгружаются.
_API_BY_URL: dict[str, Any] = {
    "etpgpb": _etpgpb_by_url,
    "mos": _mos_by_url,
    "lot_online": _lot_online_by_url,
    "tender_223": _tender_223_by_url,
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


async def fetch_api_by_url(
    page: Page,
    platform: PlatformDom,
    api_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], str | None]:
    """Закупка по URL через API площадки: ``(list_vars, detail_vars, files, inn)``.

    ``NotImplementedError`` — для ``api_format`` площадки подгрузка по URL не
    реализована (поля уровня списка из API деталей не собираются).
    """
    fmt = platform.detail.api_format
    handler = _API_BY_URL.get(fmt or "")
    if handler is None:
        raise NotImplementedError(
            f"Добавление закупки по URL для площадки «{platform.name}» ещё не реализовано"
        )
    return cast(
        tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], str | None],
        await handler(page, platform, api_fields),
    )


__all__ = ["fetch_api_by_url", "fetch_api_details", "_API_BY_URL", "_API_DETAILS"]
