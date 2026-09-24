"""Подгрузка одной закупки по явно заданному URL детальной страницы (US-5.5/FR-5.5).

Обычный обход идёт «список -> детали»: поля уровня списка (номер, предмет,
заказчик, НМЦК, даты) — из карточки выдачи поиска, детали (ОКПД2/файлы/ИНН) —
с детальной страницы/API площадки. Здесь есть только URL детальной страницы,
поэтому:

1. площадка (из совпавших по хосту) и номер закупки определяются по правилам
   ``by_url`` конфига площадки (``resolve_platform_by_url``; у площадки с
   несколькими видами страниц правил несколько — fabrikant);
2. поля уровня списка берутся с самой детальной страницы (``by_url.variables``,
   DOM-площадки) или из API площадки (``fetch_api_by_url``, API-площадки),
   детали — тем же ``extract_details``, что и в досборке перед скорингом;
3. собирается та же запись, что пишет обход (``fetch_record_by_url``).
"""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import DomByUrlRule, PlatformDom
from zakupki_parser.parser.detail import extract_details
from zakupki_parser.parser.detail_api import fetch_api_by_url
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.parser.orchestrator.activity import is_active_status


class ProcurementUrlError(ValueError):
    """URL относится к площадке, но не распознан как карточка закупки (ошибка ввода)."""


def match_by_url_rule(
    platform: PlatformDom, url: str
) -> tuple[DomByUrlRule, dict[str, str]] | None:
    """Первое правило ``by_url`` площадки, чей ``url_pattern`` совпал с URL.

    Возвращает ``(rule, groups)``, где ``groups`` — именованные группы шаблона
    (``number`` и поля запроса деталей через API). ``None`` — ни одно правило не
    совпало.
    """
    by_url = platform.by_url
    if by_url is None:
        return None
    for rule in by_url.rules:
        m = re.search(rule.url_pattern, url, flags=re.IGNORECASE)
        if m:
            groups = {k: v for k, v in m.groupdict().items() if v}
            return rule, groups
    return None


def resolve_platform_by_url(
    platforms: dict[str, PlatformDom], platform_ids: list[str], url: str
) -> tuple[str, PlatformDom, dict[str, str]]:
    """Выбирает площадку по правилам ``by_url`` среди совпавших по хосту.

    Возвращает ``(platform_id, platform, groups)``, где ``groups`` — именованные
    группы совпавшего шаблона (``number`` и поля запроса деталей через API).
    Площадки с общим хостом (44-ФЗ/223-ФЗ одного портала) различаются шаблоном —
    берётся первая совпавшая.

    ``NotImplementedError`` — ни у одной из площадок нет ``by_url`` (подгрузка
    по URL для неё не реализована); ``ProcurementUrlError`` — ``by_url`` есть,
    но URL не похож на карточку закупки (напр. ссылка на поиск или на вкладку,
    с которой карточку не собрать).
    """
    candidates = [(pid, platforms[pid]) for pid in platform_ids if pid in platforms]
    configured = [(pid, p) for pid, p in candidates if p.by_url is not None]
    if not configured:
        names = ", ".join(p.name for _, p in candidates) or ", ".join(platform_ids)
        raise NotImplementedError(
            f"Добавление закупки по URL для площадки «{names}» ещё не реализовано"
        )
    for platform_id, platform in configured:
        matched = match_by_url_rule(platform, url)
        if matched is not None:
            _, groups = matched
            return platform_id, platform, groups
    names = ", ".join(p.name for _, p in configured)
    raise ProcurementUrlError(
        f"Адрес не похож на ссылку на карточку закупки площадки «{names}» — "
        "откройте закупку на площадке и скопируйте адрес её страницы"
    )


async def fetch_record_by_url(
    page: Page,
    platform_id: str,
    platform: PlatformDom,
    url: str,
    groups: dict[str, str],
) -> dict[str, Any]:
    """Собирает запись закупки (как у обхода) по URL её детальной страницы.

    Запись — та же, что пишет обход (``_process_list_record``) вместе с деталями,
    которые обычно дособираются перед скорингом (BR-08): здесь они собираются
    сразу, т.к. страница/API всё равно уже открыты. ``detail_api`` сохраняется,
    чтобы досборка перед скорингом могла повторить запрос деталей.

    ``ProcurementUrlError`` — карточка открылась, но номер закупки не извлечён
    (без номера запись невозможна, см. ``ProcurementRepository.upsert``).
    """
    matched = match_by_url_rule(platform, url)
    rule = matched[0] if matched is not None else None
    variables = rule.variables if rule is not None else []
    defaults = rule.defaults if rule is not None else {}

    number = groups.get("number")
    api_fields = dict(groups)

    list_vars: dict[str, Any] = {}
    api_context: dict[str, Any] | None = None
    if platform.detail.api_format:
        list_vars, detail_vars, files, inn = await fetch_api_by_url(page, platform, api_fields)
        # Обработчик может вернуть точный контекст досборки деталей (напр. internal
        # id lot-online, которого нет в URL) — он приоритетнее групп шаблона.
        api_context = list_vars.pop("_api", None) or api_fields or None
    else:
        detail_vars, files, inn = await extract_details(
            page, platform, {"number": number}, url, None, page_variables=variables
        )

    record: dict[str, Any] = {**defaults}
    record.update({k: v for k, v in list_vars.items() if v is not None})
    # Детали не затирают поля уровня списка значением None (как в досборке деталей).
    record.update({k: v for k, v in detail_vars.items() if v is not None})
    number = number or record.get("number")
    if number is None or str(number).strip() == "":
        raise ProcurementUrlError(
            f"Не удалось определить номер закупки на странице площадки «{platform.name}»"
        )
    record["number"] = str(number).strip()
    record["url"] = url
    record["platform_id"] = platform_id
    if inn and not record.get("inn"):
        record["inn"] = inn
    if files:
        record["files_json"] = files
    if api_context:
        record["detail_api"] = api_context
    record["is_active"] = is_active_status(platform, record.get("status"))
    record["detail_json"] = json_safe(record)
    return record
