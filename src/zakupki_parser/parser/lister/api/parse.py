"""Парсинг item'ов JSON API списка в карточку записи (по-платформенные форматы)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from zakupki_parser.parser.handlers import handler_money as _amount

# Площадка работает в часовом поясе МСК (UTC+3) — даты карточек в этом поясе.
MSK = timezone(timedelta(hours=3))


def _iso_dt(value: Any) -> datetime | None:
    """ISO-дата из API (например '2026-08-17T16:48:00.000+03:00') -> aware datetime."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _ru_dt(value: Any) -> datetime | None:
    """Дата реестра lot-online/mos ('18.08.2026 21:14' или '17.08.2026 13:56:07', МСК) -> aware."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=MSK)
        except ValueError:
            continue
    return None


def _dt_parts(value: Any) -> datetime | None:
    """Дата tender.lot-online из частей {'date': '2026-08-18', 'time': '20:33:05', ...} -> aware."""
    if not isinstance(value, dict):
        return None
    date = str(value.get("date") or "").strip()
    time = str(value.get("time") or "").strip()
    if not date:
        return None
    try:
        if time:
            return datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=MSK)
        return datetime.fromisoformat(date).replace(tzinfo=MSK)
    except ValueError:
        return None


def _parse_lot_online_item(item: dict[str, Any]) -> dict[str, Any]:
    """Item реестра lot-online (/etp_back/procedure/list): плоские поля."""
    number = str(item.get("purchaseNumber") or "")
    direction = str(item.get("direction") or "")
    law = {
        "44fz": "44-ФЗ",
        "615pprf": "44-ФЗ",
        "tender223_market": "223-ФЗ",
        "tender223": "223-ФЗ",
    }.get(direction, "")
    list_vars: dict[str, Any] = {
        "number": number,
        "subject": item.get("purchaseObjectInfo"),
        "nmck": _amount(item.get("maxSum")),
        "customer": item.get("placerFullName"),
        # substatus — человекочитаемый статус («Прием заявок»); status — код (accept).
        "status": item.get("substatus") or "",
        "purchase_type": item.get("typeName") or "",
        "law": law,
        "publication_date": _ru_dt(item.get("publicationDateTime")),
        "deadline": _ru_dt(item.get("requestEndGiveDateTime")),
        # Регион в реестре gz отсутствует (проверено 2026-09-04) — ключ оставляем
        # пустым, чтобы обработка региона шла по общему пути (см. parser.filtering).
        "region": "",
    }
    # Детальная страница «Общая информация» (канонический URL записи).
    if number:
        list_vars["detail_path"] = f"/etp_front/procedure/view/procedure/common/{number}"
    # Внутренний id реестра (item['number']) — детали через API берут его напрямую
    # (JSON-RPC conditions.procedure.id), sphinx-резолв не нужен.
    internal_id = item.get("number")
    if internal_id:
        list_vars["_api"] = {"id": internal_id}
    return list_vars


def _etpgpb_regions(attrs: dict[str, Any]) -> str:
    """Регионы etpgpb из attributes (проверено 2026-09-04 на живом API).

    Атрибут ``region`` — строка («Омская область»), ``regions``/``lot_regions`` —
    списки (лот может находиться в нескольких регионах). Склеиваем список через
    ", "; если списка нет — строка region.

    API деталей отдаёт ``regions: [null]`` при заполненном ``lot_regions``
    (проверено 2026-09-24) — null-элементы отбрасываются (иначе регионом
    становилась строка «None»), пустой после очистки список — не источник.
    """
    for key in ("regions", "lot_regions"):
        regions = attrs.get(key) or []
        if isinstance(regions, str):
            regions = [regions]
        cleaned = [str(r).strip() for r in regions if r is not None and str(r).strip()]
        if cleaned:
            return ", ".join(dict.fromkeys(cleaned))
    value = attrs.get("region")
    return str(value).strip() if isinstance(value, str) else ""


def _etpgpb_platform_id(attrs: dict[str, Any], detail_path: str | None) -> str | None:
    """Устойчивый id закупки на площадке etpgpb (fallback номера + запрос деталей).

    ``attrs['platform_id']`` отдаётся не всегда (та же непоследовательность API,
    что и у ``registry_number`` — иногда null/отсутствует вместо ожидаемого
    значения). Запасные источники, в порядке предпочтения: последний сегмент
    ``platform_url``, затем ведущие цифры последнего сегмента ``detail_path``
    (канонический путь ``/procedures/<kind>/<id>-<slug>/`` — тот же id, что
    виден в URL закупки, напр. .../procedures/gaz/258908-zakupka-.../).
    """
    pid = attrs.get("platform_id")
    if pid:
        return str(pid)
    platform_url = attrs.get("platform_url") or ""
    if platform_url:
        return platform_url.rstrip("/").split("/")[-1]
    if detail_path:
        last_segment = detail_path.rstrip("/").split("/")[-1]
        m = re.match(r"(\d+)", last_segment)
        # "0" — плейсхолдер отсутствующего id (реальные id площадки не бывают
        # нулевыми), не настоящий номер.
        if m and m.group(1) != "0":
            return m.group(1)
    return None


def _parse_etpgpb_item(item: dict[str, Any]) -> dict[str, Any]:
    """Item API etpgpb: атрибуты в ``attributes``."""
    attrs = item.get("attributes") or {}
    list_vars: dict[str, Any] = {}
    kind = str(attrs.get("kind") or "").lower()
    # Путь детальной страницы: новый «ребрендинг»-путь (как в карточках), иначе старый.
    # Считается ДО номера — нужен как один из запасных источников platform_id.
    detail_path = attrs.get("rebranding_truncated_path") or attrs.get("truncated_path")
    reg = attrs.get("registry_number")
    # Номер — регистрационный/закупочный номер (бизнес-ключ, уникален в пределах
    # площадки). Внутренний id item'а номером НЕ является — его не подставляем.
    # Проверка ИСТИННОСТИ (не ``is not None``): API отдаёт отсутствие номера то
    # null'ом, то пустой строкой ("") — обе формы должны уходить в fallback ниже
    # (реальный случай: часть закупок «Газпром.Маркетинговые исследования»
    # получала number="" вместо null и падала в критическую ошибку «без номера»
    # в _process_list_record, минуя fallback).
    if reg:
        number = re.sub(r"^\s*№\s*", "", str(reg))
    else:
        # «Закупки.Газпром» (kind=gaz, тип «Маркетинговые исследования») номер
        # извещения не имеют вовсе (registry_number/procedure_number = null,
        # has_procedure_number = false, проверено 2026-08-31 на /api/v2/procedures/gaz/).
        # Единственный стабильный уникальный идентификатор — platform_id (см.
        # _etpgpb_platform_id — attrs['platform_id'] не всегда заполнен), поэтому
        # подставляем его. Значение неймспейсим префиксом kind, чтобы не
        # коллизировать с реальным номером (registry_number) других процедур
        # площадки (уникальность — number+platform_id).
        pid = _etpgpb_platform_id(attrs, detail_path)
        number = f"{kind}-{pid}" if pid else ""
    list_vars["number"] = number
    list_vars["subject"] = attrs.get("title")
    list_vars["nmck"] = _amount(attrs.get("amount"))
    list_vars["publication_date"] = _iso_dt(attrs.get("date_published"))
    list_vars["update_date"] = _iso_dt(attrs.get("date_last_update"))
    list_vars["deadline"] = _iso_dt(attrs.get("end_registration"))
    list_vars["customer"] = attrs.get("company_name")
    list_vars["status"] = attrs.get("stage") or ""
    list_vars["region"] = _etpgpb_regions(attrs)
    if "44" in kind:
        list_vars["law"] = "44-ФЗ"
    elif "223" in kind:
        list_vars["law"] = "223-ФЗ"
    list_vars["purchase_type"] = attrs.get("custom_procedure_type_name") or attrs.get(
        "procedure_type_name"
    )
    list_vars["detail_path"] = detail_path
    # Поля для извлечения деталей через API (/api/v2/procedures/{kind}/{platform_id}/):
    # kind — первый сегмент пути («etp»), platform_id — тот же устойчивый id, что и
    # у fallback номера (_etpgpb_platform_id). Берём тот же путь, что построил
    # detail_url (rebranding или legacy truncated), чтобы детали не терялись у
    # легаси-записей. Оркестратор забирает их из list_vars (ключ _api) до сборки записи.
    segments = [s for s in (detail_path or "").split("/") if s]
    api: dict[str, Any] = {}
    if len(segments) >= 3:
        api["kind"] = segments[1]
    api_pid = _etpgpb_platform_id(attrs, detail_path)
    if api_pid:
        api["platform_id"] = api_pid
    if api:
        list_vars["_api"] = api
    return list_vars


def _parse_mos_item(item: dict[str, Any]) -> dict[str, Any]:
    """Item реестра mos.example (Query API): needId, заказчик с ИНН прямо в списке."""
    customers = item.get("customers") or []
    customer = customers[0] if customers else {}
    creator = item.get("purchaseCreator") or {}
    need_id = item.get("needId")
    # Номер — регистрационный/закупочный номер (бизнес-ключ). needId — внутренний
    # идентификатор для деталей (см. _api), НЕ номер: его не подставляем.
    number = str(item.get("number") or "")
    list_vars: dict[str, Any] = {
        "number": number,
        "subject": item.get("name"),
        "nmck": _amount(item.get("startPrice")),
        "customer": customer.get("name") or creator.get("name"),
        "region": item.get("regionName") or "",
        "status": item.get("stateName") or "",
        "purchase_type": item.get("tenderTypeName") or "",
        "law": item.get("federalLawName") or "",
        "publication_date": _ru_dt(item.get("beginDate")),
        "deadline": _ru_dt(item.get("endDate")),
        # ИНН заказчика приходит прямо в карточке реестра — резолв через DOM не нужен.
        "inn": customer.get("inn") or creator.get("inn"),
    }
    if need_id:
        list_vars["_api"] = {"need_id": need_id}
        list_vars["detail_path"] = f"/need/{need_id}"
    return list_vars


def _parse_tender_223_item(item: dict[str, Any]) -> dict[str, Any]:
    """Item реестра tender.lot-online (api-gateway indexer): плоские поля."""
    number = str(item.get("eisNumber") or item.get("etpNumber") or "")
    lot = item.get("lotNumber")
    list_vars: dict[str, Any] = {
        "number": number,
        "subject": item.get("title"),
        "nmck": _amount(item.get("price")),
        "customer": item.get("organizationTitle") or item.get("organizationShortTitle"),
        "status": item.get("status") or "",
        "purchase_type": item.get("purchaseMethod") or "",
        # Платформа 223-ФЗ по определению (как в DOM-карточке).
        "law": "223-ФЗ",
        # Регион заказчика прямо в реестре indexer (проверено 2026-09-04 на живом
        # API): customerOkato («регион, г», «Свердловская, обл»), fallback regionOkato.
        "region": str(item.get("customerOkato") or item.get("regionOkato") or ""),
        "publication_date": _dt_parts(item.get("publicationDate")),
        "deadline": _dt_parts(item.get("demandEndDate")),
    }
    if number and lot is not None:
        list_vars["detail_path"] = f"/procedure?procedureNumber={number}&lotNumber={lot}"
        # Детали через открытый API /api-gateway/etp/procedure/{номер}/{лот}
        # (detail.api_format: tender_223) — номер и лот как ключи запроса.
        list_vars["_api"] = {"number": number, "lot": str(lot)}
    return list_vars


def parse_api_item(item: dict[str, Any], fmt: str = "etpgpb") -> dict[str, Any]:
    """Маппит один item JSON API списка в dict переменных карточки (list.variables).

    Поля соответствуют именам ``list_config.variables``, чтобы дальше записи шли
    по общему пути (детальная страница, stop-условия, скоринг, сохранение).
    Формат item'а выбирается ``search.api_item_format``.
    """
    if fmt == "lot_online":
        return _parse_lot_online_item(item)
    if fmt == "mos":
        return _parse_mos_item(item)
    if fmt == "tender_223":
        return _parse_tender_223_item(item)
    return _parse_etpgpb_item(item)
