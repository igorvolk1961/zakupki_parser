"""Мониторинг фоновой индексации по ОКПД2 для devops: очереди, индекс, ресурсы хоста."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, Depends

from zakupki_parser.api.app.deps import ApiContext


def build_monitoring_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    require_devops = ctx.require_devops

    @router.get(
        "/api/devops/monitoring",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def monitoring() -> dict[str, Any]:
        """Сводка для вкладки «Мониторинг»: очереди каскада, наполнение индекса, ресурсы.

        Все три блока — best-effort: недоступность транспорта или БД не роняет
        эндпоинт целиком, а отражается в соответствующем блоке ответа.
        """
        if state.score_transport is not None:
            queues = await state.score_transport.queue_status()
        else:
            queues = {"available": False}

        indexing = state.cfg.service.indexing
        if state.repository is not None:
            index_counts = await _repo().index_status_counts()
            recent_errors = await _repo().recent_index_errors()
        else:
            index_counts = {}
            recent_errors = []

        disk = psutil.disk_usage(str(Path(state.configs_dir).resolve()))
        resources = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory": _mem_stats(psutil.virtual_memory()),
            "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        }

        return {
            "queues": queues,
            "index": {
                "enabled": indexing.enabled,
                "okpd2_prefixes": indexing.okpd2_prefixes,
                "counts": index_counts,
                "recent_errors": recent_errors,
            },
            "resources": resources,
        }

    return router


def _mem_stats(mem: Any) -> dict[str, float]:
    return {"total": mem.total, "used": mem.used, "percent": mem.percent}
