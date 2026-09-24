"""Детали lot-online (gz, 44-ФЗ): JSON-RPC /etp_back/api/get."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _post_json
from zakupki_parser.parser.handlers import handler_money as _amount
from zakupki_parser.parser.lister.api.parse import _ru_dt

# Поля sphinx-запроса gz для подгрузки по URL: подмножество профиля карточки
# «Общая информация» (проверено 2026-09-24 на живом API). ``rules`` —
# ``Procedure.Info`` (предмет/статус/даты в удобном формате) +
# ``Procedure.CommonData`` (заказчик/ИНН/регион/направление); ``post`` —
# под-объекты, нужные CommonData.
_SPHINX_FIELDS = [
    "procedure.status",
    "procedure.substatus",
    "procedure.purchaseNumber",
    "procedure.purchaseObjectInfo",
    "procedure.number",
    "procedure.direction",
    "procedure.placer.fullName",
    "procedure.placer.inn",
    "procedure.deliveryAddress",
    "procedure.publicationDateTime",
    "procedure.requestEndGiveDateTime",
    "procedure.href",
]
_SPHINX_RULES = ["Procedure.Info", "Procedure.CommonData"]
_SPHINX_POST = [
    "Procedure.Pprf615CommonInfo",
    "Procedure.CriteriaInfo",
    "Procedure.FilteredProvisionAmount",
    "Procedure.FinancialServicesInfo",
    "Procedure.PurchaseView",
]


def _okpd_from_items(items: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Коды и названия ОКПД2 из позиций лота (уникальные, в порядке появления)."""
    codes = list(dict.fromkeys(str(it["okpd2Code"]) for it in items if it.get("okpd2Code")))
    names = list(dict.fromkeys(str(it["okpd2Name"]) for it in items if it.get("okpd2Name")))
    return (",".join(codes) if codes else None), " | ".join(names)


async def _fetch_lot_info(page: Page, platform: PlatformDom, proc_number: Any) -> dict[str, Any]:
    """lotInfo лота по внутреннему id (JSON-RPC ``Purchase.LotInfo``) — ОКПД2/НМЦК/позиции."""
    base = platform.url.rstrip("/")
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
    if not entities:
        return {}
    return (entities[0].get("procedure") or {}).get("lotInfo") or {}


def _detail_vars_from_lot_info(lot_info: dict[str, Any]) -> dict[str, Any]:
    """``detail_vars`` из lotInfo: ОКПД2, НМЦК и (если есть) регион."""
    items = lot_info.get("items") or []
    code, name = _okpd_from_items(items)
    detail_vars: dict[str, Any] = {
        "okpd2_code": code,
        "okpd2_name": name,
        "nmck": _amount(lot_info.get("maxSum")),
    }
    # Регион gz: в реестре и lotInfo региона обычно НЕТ (2026-09-04). Ключ
    # оставлен как страховка на случай появления поля в API.
    region = lot_info.get("region")
    if region:
        detail_vars["region"] = str(region)
    return detail_vars


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
    lot_info = await _fetch_lot_info(page, platform, proc_number)
    return _detail_vars_from_lot_info(lot_info), [], None


async def _lot_online_by_url(
    page: Page,
    platform: PlatformDom,
    api_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], str | None]:
    """Закупка gz lot-online (44-ФЗ) по URL детальной страницы (US-5.5).

    Два запроса JSON-RPC: sphinx ``Procedure.Info``+``Procedure.CommonData`` по
    ``purchaseNumber`` из URL — поля уровня списка (номер, предмет, заказчик/ИНН,
    статус, регион-место поставки, даты; проверено 2026-09-24 на живом API);
    ``Purchase.LotInfo`` по внутреннему id — ОКПД2/НМЦК (``detail_vars``).
    ``purchase_type`` в этих ответах не отдаётся (только код ``type``) — пусто.

    Возвращает ``(list_vars, detail_vars, files, inn)``; ``_api`` — внутренний
    id для досборки деталей перед скорингом (BR-08).
    """
    number = str(api_fields.get("number") or "")
    base = platform.url.rstrip("/")
    resolved = await _post_json(
        page,
        f"{base}/etp_back/api/get",
        {
            "manager": "sphinx",
            "entity": "Procedure",
            "alias": "procedure",
            "fields": _SPHINX_FIELDS,
            "conditions": {"procedure.purchaseNumber": number},
            "rules": _SPHINX_RULES,
            "post": _SPHINX_POST,
        },
    )
    entities = ((resolved.get("data") or {}).get("entities")) or []
    entity: dict[str, Any] = (entities[0].get("procedure") or {}) if entities else {}
    placer = entity.get("placer") or {}
    internal_id = entity.get("number")
    list_vars: dict[str, Any] = {
        "number": entity.get("purchaseNumber") or number,
        "subject": entity.get("purchaseObjectInfo"),
        "customer": placer.get("fullName"),
        "inn": placer.get("inn"),
        "status": entity.get("substatus") or entity.get("status") or "",
        "region": entity.get("deliveryAddress") or "",
        "publication_date": _ru_dt(entity.get("publicationDateTime")),
        "deadline": _ru_dt(entity.get("requestEndGiveDateTime")),
    }
    if internal_id:
        list_vars["_api"] = {"id": internal_id}
    lot_info = await _fetch_lot_info(page, platform, internal_id) if internal_id else {}
    detail_vars = _detail_vars_from_lot_info(lot_info)
    return list_vars, detail_vars, [], None
