"""Детали tender.lot-online (223-ФЗ): GET /api-gateway/etp/procedure/{номер}/{лот}."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _get_json
from zakupki_parser.parser.handlers import handler_money as _amount


async def _tender_223_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Детали tender.lot-online (223-ФЗ): GET /api-gateway/etp/procedure/{номер}/{лот}.

    Открытый API (без авторизации, проверено 2026-08-25): ОКПД2 —
    ``productionNomenclatures[].okpd2Title``, заказчик/ИНН — ``organization``/
    ``customers``, НМЦК — ``commonInfo.price``, статус — ``commonInfo.stage``,
    файлы — ``notices[].fileSignResponse[].fileDTO`` (скачивание —
    ``/etp/downloadppf?uuid=...``). Номер и лот приходят в реестре indexer'а.
    """
    fields = api_fields or {}
    number = str(fields.get("number") or "")
    lot = str(fields.get("lot") or "")
    if not number or not lot:
        return {}, [], None
    base = platform.url.rstrip("/")
    payload = await _get_json(page, f"{base}/api-gateway/etp/procedure/{number}/{lot}")
    ci = payload.get("commonInfo") or {}
    org = payload.get("organization") or {}
    customers = payload.get("customers") or []
    customer = customers[0] if customers else org
    inn: str | None = customer.get("inn") or org.get("inn")

    codes: list[str] = []
    names: list[str] = []
    for nom in payload.get("productionNomenclatures") or []:
        title = str(nom.get("okpd2Title") or "").strip()
        if not title:
            continue
        m = re.match(r"^(\d{2}(?:\.\d+)*)", title)
        if m:
            code = m.group(1)
            if code not in codes:
                codes.append(code)
            name = title[m.end() :].lstrip(" :")
            if name and name not in names:
                names.append(name)

    files = [
        {
            "name": str((fs.get("fileDTO") or {}).get("fileName") or ""),
            "url": f"{base}/etp/downloadppf?uuid={fs['fileDTO']['uuid']}",
        }
        for notice in payload.get("notices") or []
        for fs in notice.get("fileSignResponse") or []
        if (fs.get("fileDTO") or {}).get("uuid")
    ]
    detail_vars: dict[str, Any] = {
        "okpd2_code": ",".join(codes) if codes else None,
        "okpd2_name": " | ".join(names),
        "customer": customer.get("title") or org.get("title"),
        "status": (ci.get("stage") or {}).get("title") or "",
        "nmck": _amount(ci.get("price")),
    }
    # Регион — commonInfo.customerOkato («Москва, г»), fallback regionOkato
    # (проверено 2026-09-04 на живом API /api-gateway/etp/procedure/{номер}/{лот}).
    region = ci.get("customerOkato") or ci.get("regionOkato") or None
    if region:
        detail_vars["region"] = str(region)
    return detail_vars, files, inn
