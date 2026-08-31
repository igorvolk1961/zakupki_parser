"""Эндпоинт оценки покрытия полей конфигурациями площадок (devops).

Статическое покрытие (что задекларировано в конфиге) вычисляется без БД;
динамическое (доля реально заполненных записей) — по сохранённым закупкам.
Read-only; используется как диагностика полноты конфигов и как эвристика для
будущего авто-генератора конфигураций.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.config.models.fields import (
    coverage_score,
    missing_mandatory,
    static_field_coverage,
)

# Полное агрегирование по таблице procurements — дорогая операция; кэшируем результат
# на короткое время, чтобы частые запросы диагностики не сканировали всю таблицу.
RuntimeData = dict[str, dict[str, object]]
_RUNTIME_CACHE_TTL_SECONDS = 60.0
_runtime_cache: dict[str, RuntimeData | float | None] = {"ts": 0.0, "data": None}


def build_coverage_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    require_devops = ctx.require_devops

    async def _runtime_aggregate() -> RuntimeData:
        """Заполненность по площадкам (с кэшем; не кэшируем состояние «БД недоступна»)."""
        if state.repository is None:
            return {}
        ts = _runtime_cache["ts"]
        data = _runtime_cache["data"]
        if (
            isinstance(data, dict)
            and isinstance(ts, float)
            and time.monotonic() - ts < _RUNTIME_CACHE_TTL_SECONDS
        ):
            return data
        rows = await state.repository.field_coverage_runtime()
        fresh: RuntimeData = {row["platform_id"]: row for row in rows}
        _runtime_cache["ts"] = time.monotonic()
        _runtime_cache["data"] = fresh
        return fresh

    @router.get(
        "/api/coverage",
        dependencies=[Depends(require_devops)],
    )
    async def coverage() -> dict[str, object]:
        """Покрытие полей по каждой площадке: статика (конфиг) + динамика (БД)."""
        platforms = state.cfg.dom.platforms or {}
        runtime_by = await _runtime_aggregate()
        result: list[object] = []
        for pid, platform in platforms.items():
            static = static_field_coverage(platform)
            result.append(
                {
                    "platform_id": pid,
                    "coverage_score": coverage_score(static),
                    "missing_mandatory": missing_mandatory(static),
                    "static": [
                        {
                            "key": f.key,
                            "label": f.label,
                            "tier": f.tier.value,
                            "status": f.status,
                            "sources": f.sources,
                        }
                        for f in static
                    ],
                    "runtime": runtime_by.get(pid),
                }
            )
        return {"platforms": result}

    return router
