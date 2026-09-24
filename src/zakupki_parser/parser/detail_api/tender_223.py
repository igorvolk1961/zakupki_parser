"""Детали tender.lot-online (223-ФЗ): GET /api-gateway/etp/procedure/{номер}/{лот}."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _get_json
from zakupki_parser.parser.handlers import MSK
from zakupki_parser.parser.handlers import handler_money as _amount
from zakupki_parser.parser.lister.api.parse import _iso_dt


def _parse_details(
    payload: dict[str, Any], base: str
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Разбор ответа /api-gateway/etp/procedure/{номер}/{лот} в ``(detail_vars, files, inn)``.

    Открытый API (без авторизации, проверено 2026-08-25): ОКПД2 —
    ``productionNomenclatures[].okpd2Title``, заказчик/ИНН — ``organization``/
    ``customers``, НМЦК — ``commonInfo.price``, статус — ``commonInfo.stage``,
    файлы — ``notices[].fileSignResponse[].fileDTO`` (скачивание —
    ``/etp/downloadppf?uuid=...``). Регион — ``commonInfo.customerOkato``,
    fallback ``regionOkato`` (проверено 2026-09-04 на живом API).
    """
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
    region = ci.get("customerOkato") or ci.get("regionOkato") or None
    if region:
        detail_vars["region"] = str(region)
    return detail_vars, files, inn


def _stage_datetime(stages: Any, code: str) -> datetime | None:
    """Дата+время этапа процедуры по его ``code`` (напр. GD_END — окончание подачи)."""
    for stage in stages or []:
        for item in stage.get("stageList") or []:
            if item.get("code") == code and item.get("date"):
                text = str(item["date"])
                if item.get("time"):
                    text = f"{text} {item['time']}"
                try:
                    if item.get("time"):
                        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
                    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=MSK)
                except ValueError:
                    return None
    return None


async def _tender_223_details(
    page: Page,
    platform: PlatformDom,
    list_vars: dict[str, Any],
    api_fields: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Детали tender.lot-online (223-ФЗ): GET /api-gateway/etp/procedure/{номер}/{лот}."""
    fields = api_fields or {}
    number = str(fields.get("number") or "")
    lot = str(fields.get("lot") or "")
    if not number or not lot:
        return {}, [], None
    base = platform.url.rstrip("/")
    payload = await _get_json(page, f"{base}/api-gateway/etp/procedure/{number}/{lot}")
    return _parse_details(payload, base)


async def _tender_223_by_url(
    page: Page,
    platform: PlatformDom,
    api_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], str | None]:
    """Закупка tender.lot-online (223-ФЗ) по URL детальной страницы (US-5.5).

    Один запрос ``/api-gateway/etp/procedure/{номер}/{лот}`` отдаёт и поля уровня
    списка (``commonInfo``: номер/предмет/НМЦК/статус/способ/регион; заказчик —
    ``customers``/``organization``; даты — ``notices[].publishDate`` и этап
    ``GD_END``; проверено 2026-09-24 на живом API), и детали (см. ``_parse_details``).

    Возвращает ``(list_vars, detail_vars, files, inn)``.
    """
    number = str(api_fields.get("number") or "")
    lot = str(api_fields.get("lot") or "")
    base = platform.url.rstrip("/")
    payload = await _get_json(page, f"{base}/api-gateway/etp/procedure/{number}/{lot}")
    ci = payload.get("commonInfo") or {}
    org = payload.get("organization") or {}
    customers = payload.get("customers") or []
    customer = customers[0] if customers else org
    notices = payload.get("notices") or []
    list_vars: dict[str, Any] = {
        "number": ci.get("eisNumber") or number,
        "subject": ci.get("title"),
        "nmck": _amount(ci.get("price")),
        "customer": customer.get("title") or org.get("title"),
        "status": (ci.get("stage") or {}).get("title") or "",
        "purchase_type": ci.get("purchaseMethod") or ci.get("purchaseCategory") or "",
        "region": str(ci.get("customerOkato") or ci.get("regionOkato") or ""),
        "publication_date": _iso_dt((notices[0] or {}).get("publishDate")) if notices else None,
        "deadline": _stage_datetime(payload.get("stages"), "GD_END"),
    }
    detail_vars, files, inn = _parse_details(payload, base)
    return list_vars, detail_vars, files, inn
