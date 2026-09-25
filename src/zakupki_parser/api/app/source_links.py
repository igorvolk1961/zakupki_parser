"""Связь условий отчётных полей с сайтами-источниками.

- Сайты из условий (``value_kind=url``) и сайт поставщика ставятся в сбор при
  сохранении профиля (``ensure_profile_sources``).
- Анализу и пересчёту условий к URL-условию добавляются сведения о сайте
  (``condition["source"]``: нормализованный URL, статус, полнота текста, время
  сбора) — текст сайта они читают из хранилища сами
  (``scoring_common.sources.matching.source_contexts``). В профиле эти
  сведения не хранятся.
- Когда сбор сайта закончился — условия профилей, которые на него ссылаются,
  пересчитываются без LLM (``recheck_profiles_for_source``): до этого они
  были «не проверено: сайт собирается».
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from scoring_common.sources.urls import normalize_source_url
from zakupki_parser.api.app.state import AppState
from zakupki_parser.storage.db import SiteSource

logger = logging.getLogger(__name__)


def condition_urls(report_fields: Iterable[Mapping[str, Any]]) -> list[str]:
    """URL сайтов из условий полей (без повторов)."""
    urls: dict[str, str] = {}
    for field in report_fields:
        condition = field.get("condition") or {}
        if condition.get("value_kind") == "url" and condition.get("value"):
            url = str(condition["value"])
            urls.setdefault(normalize_source_url(url), url)
    return list(urls.values())


def source_meta(source: SiteSource | None, url_norm: str, active: bool) -> dict[str, Any]:
    if source is None:
        return {"url_norm": url_norm, "status": "missing", "active": active}
    return {
        "id": source.id,
        "url_norm": source.url_norm,
        "status": source.status,
        "active": active,
        "text_complete": source.text_complete,
        "fetched_at": source.fetched_at.isoformat() if source.fetched_at else None,
        "progress": dict(source.progress or {}),
    }


async def with_source_meta(
    state: AppState, report_fields: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Копия полей, где у URL-условий есть ``condition["source"]``."""
    repo = state.repository
    manager = state.source_crawls
    out: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    for field in report_fields:
        field = dict(field)
        condition = dict(field.get("condition") or {})
        if condition.get("value_kind") == "url" and repo is not None:
            url_norm = normalize_source_url(str(condition.get("value") or ""))
            if url_norm not in cache:
                source = await repo.get_site_source_by_url(url_norm)
                active = bool(manager and source and manager.is_active(source.id))
                cache[url_norm] = source_meta(source, url_norm, active)
            condition["source"] = cache[url_norm]
            field["condition"] = condition
        out.append(field)
    return out


async def ensure_profile_sources(state: AppState, profile: Any) -> None:
    """Сайты профиля (из условий и сайт поставщика) — в сбор, если не собраны.

    Не мешает сохранению профиля: недоступный/небезопасный адрес — в лог.
    """
    manager = state.source_crawls
    if manager is None:
        return
    urls = condition_urls(profile.report_fields or [])
    website = (getattr(profile, "website_url", None) or "").strip()
    if website:
        urls.append(website)
    for url in urls:
        try:
            await manager.ensure(url)
        except Exception as exc:  # noqa: BLE001
            logger.info("Сайт %s профиля %s не поставлен в сбор: %s", url, profile.id, exc)


async def recheck_profiles_for_source(state: AppState, url_norm: str) -> None:
    """Сбор сайта закончился — пересчитать условия профилей, которые на него ссылаются.

    Профиль при этом не менялся: отчёты, актуальные до пересчёта, остаются
    актуальными (``snapshot_from = snapshot_to = profiles.updated_at``).
    """
    from zakupki_parser.api.app.condition_recheck import start_condition_recheck

    repo = state.repository
    if repo is None:
        return
    for profile in await repo.list_profiles_with_url_conditions():
        urls = {normalize_source_url(u) for u in condition_urls(profile.report_fields or [])}
        if url_norm in urls:
            logger.info("Сайт %s собран — пересчёт условий профиля %s", url_norm, profile.id)
            start_condition_recheck(
                state, profile, snapshot_from=profile.updated_at, keep_fresh=True
            )
