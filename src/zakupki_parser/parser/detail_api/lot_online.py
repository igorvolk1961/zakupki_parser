"""Детали lot-online (gz, 44-ФЗ): JSON-RPC /etp_back/api/get."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom
from zakupki_parser.parser.detail_api.http import _post_json


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
    detail_vars: dict[str, Any] = {"okpd2_code": code, "okpd2_name": name}
    # Регион gz: в реестре и lotInfo региона НЕТ (2026-09-04). «Место поставки»
    # извлекается из DOM common-страницы по явному региональному запросу профиля
    # (detail.region_on_demand_dom в lot_online_44.yaml + set_score). Ключ lot_info.region
    # оставлен как страховка на случай появления поля в API.
    region = lot_info.get("region")
    if region:
        detail_vars["region"] = str(region)
    return detail_vars, [], None
