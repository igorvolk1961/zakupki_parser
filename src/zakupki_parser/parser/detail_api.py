"""Извлечение деталей закупки через открытые API площадок (вместо DOM).

Площадки с ``detail.api_format`` (lot_online, etpgpb, ...) отдают поля деталей
(ОКПД2, позиции, заказчик с ИНН, НМЦК, файлы) по JSON API без открытия
браузерной страницы. Функции здесь возвращают ``(detail_vars, files, inn)``:
  - ``detail_vars`` — dict имён переменных (те же, что извлекались бы из DOM),
  - ``files`` — список ``{"name": ..., "url": ...}``,
  - ``inn`` — ИНН заказчика из API (None — резолвить как раньше через DOM).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.handlers import handler_money as _amount
from zakupki_parser.parser.lister.api import request_json

logger = logging.getLogger(__name__)


async def _post_json(page: Page, url: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST JSON-RPC запрос и разбор JSON-ответа (сбой — исключение для retry)."""
    return cast(
        dict[str, Any], await request_json(page, "POST", url, body=body, label="API деталей")
    )


async def _get_json(page: Page, url: str) -> dict[str, Any]:
    """GET JSON-запрос (сбой — исключение для retry)."""
    return cast(dict[str, Any], await request_json(page, "GET", url, label="API деталей"))


def _okpd_from_items(items: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Коды и названия ОКПД2 из позиций лота (уникальные, в порядке появления)."""
    codes = list(dict.fromkeys(str(it["okpd2Code"]) for it in items if it.get("okpd2Code")))
    names = list(dict.fromkeys(str(it["okpd2Name"]) for it in items if it.get("okpd2Name")))
    return (",".join(codes) if codes else None), " | ".join(names)


async def _lot_online_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Детали lot-online (gz, 44-ФЗ): JSON-RPC /etp_back/api/get.

    Внутренний id приходит прямо в реестре (item['number'] — см. _parse_lot_online_item),
    поэтому шаг sphinx-резолва выполняется только как fallback (если id не было).
    """
    number = str(list_vars.get("number") or "")
    if not number:
        return {}, [], None
    base = platform.url.rstrip("/")
    proc_number = (api_fields or {}).get("id")
    if proc_number is None:
        # Fallback: резолв purchaseNumber -> внутренний id (manager sphinx).
        resolved = await _post_json(
            page,
            f"{base}/etp_back/api/get",
            {
                "manager": "sphinx",
                "entity": "Procedure",
                "alias": "procedure",
                "fields": ["procedure.number", "procedure.type"],
                "conditions": {"procedure.purchaseNumber": number},
                "rules": ["Procedure.Info"],
            },
        )
        entities = ((resolved.get("data") or {}).get("entities")) or []
        proc_number = (entities[0].get("procedure") or {}).get("number") if entities else None
    if proc_number is None:
        raise RuntimeError("API деталей lot-online: номер не разрешился")
    info = await _post_json(
        page,
        f"{base}/etp_back/api/get",
        {
            "manager": "procedures",
            "entity": "Purchase",
            "alias": "procedure",
            "fields": [],
            "conditions": {"procedure.id": proc_number},
            "rules": ["Purchase.LotInfo"],
            "post": ["Procedure.LotInfo", "Procedure.PurchaseView"],
        },
    )
    entities = ((info.get("data") or {}).get("entities")) or []
    lot_info: dict[str, Any] = {}
    if entities:
        lot_info = (entities[0].get("procedure") or {}).get("lotInfo") or {}
    items = lot_info.get("items") or []
    code, name = _okpd_from_items(items)
    return {"okpd2_code": code, "okpd2_name": name}, [], None


async def _etpgpb_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Детали etpgpb: GET /api/v2/procedures/{kind}/{platform_id}/ (JSON:API).

    ОКПД2 — из included.nomenclature, заказчик/ИНН — included.company по
    relationships.company.id, файлы — included.doc (имя + URL скачивания).
    """
    fields = api_fields or {}
    kind = fields.get("kind")
    platform_id = fields.get("platform_id")
    if not kind or not platform_id:
        return {}, [], None
    url = f"{platform.url.rstrip('/')}/api/v2/procedures/{kind}/{platform_id}/"
    payload = await _get_json(page, url)
    data = payload.get("data") or {}
    included = payload.get("included") or []
    by_type: dict[str, list[dict[str, Any]]] = {}
    for entry in included:
        by_type.setdefault(entry.get("type"), []).append(entry)

    codes = list(
        dict.fromkeys(
            str((n.get("attributes") or {}).get("code"))
            for n in by_type.get("nomenclature", [])
            if (n.get("attributes") or {}).get("code")
        )
    )
    names = list(
        dict.fromkeys(
            str((n.get("attributes") or {}).get("name"))
            for n in by_type.get("nomenclature", [])
            if (n.get("attributes") or {}).get("name")
        )
    )

    company_id = ((data.get("relationships") or {}).get("company") or {}).get("data") or {}
    company_id = company_id.get("id") if isinstance(company_id, dict) else None
    customer: str | None = None
    inn: str | None = None
    for comp in by_type.get("company", []):
        if company_id is not None and comp.get("id") != company_id:
            continue
        attrs = comp.get("attributes") or {}
        customer = attrs.get("full_name")
        inn = attrs.get("inn")
        break

    lots = by_type.get("lot", [])
    lot_attrs = (lots[0].get("attributes") or {}) if lots else {}
    attrs = data.get("attributes") or {}
    files = [
        {"name": (d.get("attributes") or {}).get("file_name") or "", "url": d["attributes"]["url"]}
        for d in by_type.get("doc", [])
        if (d.get("attributes") or {}).get("url")
    ]
    detail_vars: dict[str, Any] = {
        "okpd2_code": ",".join(codes) if codes else None,
        "okpd2_name": " | ".join(names),
        "customer": customer,
        "status": lot_attrs.get("name_status") or attrs.get("stage") or "",
        "nmck": _amount(attrs.get("amount")),
    }
    return detail_vars, files, inn


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


_API_DETAILS: dict[str, Any] = {
    "lot_online": _lot_online_details,
    "etpgpb": _etpgpb_details,
    "mos": _mos_details,
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
