"""Детали etpgpb: GET /api/v2/procedures/{kind}/{platform_id}/ (JSON:API)."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _get_json
from zakupki_parser.parser.handlers import handler_money as _amount


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
    # Регион — пробное извлечение (из attributes компании или карточки процедуры).
    # TODO: verify — имя поля API не подтверждено на живом сайте; если регион не
    # найден, ключ не ставится (значение detail_json останется без region).
    region: str | None = None
    for comp in by_type.get("company", []):
        if company_id is not None and comp.get("id") != company_id:
            continue
        cattrs = comp.get("attributes") or {}
        region = cattrs.get("region") or cattrs.get("regionName")
        if region:
            break
    if not region:
        region = attrs.get("region") or attrs.get("regionName")
    if region:
        detail_vars["region"] = str(region)
    return detail_vars, files, inn
