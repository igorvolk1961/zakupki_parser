"""Детали mos.example: GET /newapi/api/Need/Get?needId= — ОКПД2, файлы (FileStorage)."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _get_json
from zakupki_parser.parser.handlers import handler_money as _amount
from zakupki_parser.parser.lister.api.parse import _ru_dt


def _parse_details(
    payload: dict[str, Any], base: str
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Разбор Need/Get в ``(detail_vars, files)`` — ОКПД2, заказчик, статус, НМЦК, файлы."""
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
    return detail_vars, files


async def _mos_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Детали mos.example: GET /newapi/api/Need/Get?needId= — ОКПД2, файлы (FileStorage).

    ИНН заказчика отдаёт уже API списка (в list_vars['inn']), здесь не дублируется.
    """
    need_id = (api_fields or {}).get("need_id")
    if not need_id:
        return {}, [], None
    base = platform.url.rstrip("/")
    payload = await _get_json(page, f"{base}/newapi/api/Need/Get?needId={need_id}")
    detail_vars, files = _parse_details(payload, base)
    return detail_vars, files, None


async def _mos_by_url(
    page: Page,
    platform: PlatformDom,
    api_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], str | None]:
    """Закупка mos.ru по URL детальной страницы (US-5.5): поля списка + детали.

    Один запрос ``/newapi/api/Need/Get?needId=`` отдаёт всё: номер (``id`` ==
    needId == ``number`` реестра, проверено 2026-09-24), предмет (``name``),
    заказчика, НМЦК, регион, статус, закон и даты (``proposalStartDate`` —
    публикация, ``proposalEndDate`` — окончание подачи), а также ОКПД2/файлы
    (см. ``_parse_details``). ``purchase_type`` (``tenderTypeName`` в реестре) в
    этом ответе не отдаётся — поле остаётся пустым.

    Возвращает ``(list_vars, detail_vars, files, inn)``.
    """
    need_id = api_fields.get("need_id")
    base = platform.url.rstrip("/")
    payload = await _get_json(page, f"{base}/newapi/api/Need/Get?needId={need_id}")
    region = payload.get("region")
    region_name = region.get("name") if isinstance(region, dict) else region
    list_vars: dict[str, Any] = {
        "number": str(payload.get("id") or ""),
        "subject": payload.get("name"),
        "nmck": _amount(payload.get("nmck")),
        "customer": (payload.get("customer") or {}).get("name"),
        "region": (region_name or "").strip(),
        "status": (payload.get("state") or {}).get("name") or "",
        "law": payload.get("federalLawName") or "",
        "publication_date": _ru_dt(payload.get("proposalStartDate")),
        "deadline": _ru_dt(payload.get("proposalEndDate")),
    }
    detail_vars, files = _parse_details(payload, base)
    return list_vars, detail_vars, files, None
