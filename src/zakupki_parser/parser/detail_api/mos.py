"""Детали mos.ru: GET /newapi/api/Need/Get?needId= — ОКПД2, файлы (FileStorage)."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _get_json
from zakupki_parser.parser.handlers import handler_money as _amount


async def _mos_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Детали mos.ru: GET /newapi/api/Need/Get?needId= — ОКПД2, файлы (FileStorage).

    ИНН заказчика отдаёт уже API списка (в list_vars['inn']), здесь не дублируется.
    """
    need_id = (api_fields or {}).get("need_id")
    if not need_id:
        return {}, [], None
    base = platform.url.rstrip("/")
    payload = await _get_json(page, f"{base}/newapi/api/Need/Get?needId={need_id}")
    items = payload.get("items") or []
    codes = list(
        dict.fromkeys(str(it["okpd"]["code"]) for it in items if (it.get("okpd") or {}).get("code"))
    )
    names = list(
        dict.fromkeys(str(it["okpd"]["name"]) for it in items if (it.get("okpd") or {}).get("name"))
    )
    files = [
        {
            "name": f.get("name") or "",
            "url": f"{base}/newapi/api/FileStorage/Download?id={f['id']}",
        }
        for f in payload.get("files") or []
        if f.get("id")
    ]
    detail_vars: dict[str, Any] = {
        "okpd2_code": ",".join(codes) if codes else None,
        "okpd2_name": " | ".join(names),
        "customer": (payload.get("customer") or {}).get("name"),
        "status": (payload.get("state") or {}).get("name"),
        "nmck": _amount(payload.get("nmck")),
    }
    return detail_vars, files, None
