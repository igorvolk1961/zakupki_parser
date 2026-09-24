"""Детали etpgpb: GET /api/v2/procedures/{kind}/{platform_id}/ (JSON:API)."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _get_json
from zakupki_parser.parser.handlers import handler_datetime
from zakupki_parser.parser.handlers import handler_money as _amount
from zakupki_parser.parser.lister.api.parse import _etpgpb_regions, _parse_etpgpb_item


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
    payload = await _get_json(page, _procedure_url(platform, kind, platform_id))
    return _parse_details(payload)


async def _etpgpb_by_url(
    page: Page,
    platform: PlatformDom,
    api_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], str | None]:
    """Закупка etpgpb по URL детальной страницы (US-5.5): поля списка + детали.

    Тот же запрос, что у ``_etpgpb_details`` (/api/v2/procedures/{kind}/{platform_id}/):
    ``data`` ответа — тот же item процедуры, что и в API списка, поэтому поля
    уровня списка разбираются общим ``_parse_etpgpb_item``. Отличия ответа
    деталей от списка (проверено 2026-09-24 на живом API): нет ``kind`` (берётся
    из сегмента URL), ``end_registration`` и ``company_name`` (срок подачи —
    ``application_reception_normal_date`` «ДД.ММ.ГГГГ ЧЧ:ММ» МСК, заказчик — из
    included.company, см. ``_parse_details``).

    Возвращает ``(list_vars, detail_vars, files, inn)``.
    """
    kind = api_fields["kind"]
    platform_id = api_fields["platform_id"]
    payload = await _get_json(page, _procedure_url(platform, kind, platform_id))
    data = payload.get("data") or {}
    attrs = dict(data.get("attributes") or {})
    attrs["kind"] = attrs.get("kind") or kind
    list_vars = _parse_etpgpb_item({**data, "attributes": attrs})
    list_vars.pop("_api", None)
    if list_vars.get("deadline") is None:
        list_vars["deadline"] = handler_datetime(attrs.get("application_reception_normal_date"))
    if not list_vars.get("law"):
        list_vars["law"] = _law_by_registry_number(list_vars.get("number"))
    detail_vars, files, inn = _parse_details(payload)
    return list_vars, detail_vars, files, inn


def _procedure_url(platform: PlatformDom, kind: str, platform_id: Any) -> str:
    return f"{platform.url.rstrip('/')}/api/v2/procedures/{kind}/{platform_id}/"


def _law_by_registry_number(number: Any) -> str | None:
    """Закон по формату реестрового номера ЕИС: 19 цифр — 44-ФЗ, 11 цифр — 223-ФЗ.

    Нужен при подгрузке по URL: в API списка закон виден по ``kind`` (fz223/…),
    а в ответе деталей ``kind`` нет, сегмент URL (``etp``/``gaz``) закона не несёт.
    Номера вне ЕИС (коммерческие, «ГП632202») — закон не определяется.
    """
    text = str(number or "")
    if re.fullmatch(r"\d{19}", text):
        return "44-ФЗ"
    if re.fullmatch(r"\d{11}", text):
        return "223-ФЗ"
    return None


def _parse_details(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Разбор ответа /api/v2/procedures/{kind}/{platform_id}/ в ``(detail_vars, files, inn)``."""
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
    # Регион — attributes процедуры (проверено 2026-09-04 на живом API):
    # attrs.region («Омская область») / attrs.regions / attrs.lot_regions (списки,
    # лот в нескольких регионах). Если поля пусты — ключ не ставится.
    region = _etpgpb_regions(attrs)
    if region:
        detail_vars["region"] = region
    return detail_vars, files, inn
