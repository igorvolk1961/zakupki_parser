"""Мониторинг фоновой индексации по ОКПД2 для devops: очереди, индекс, ресурсы хоста."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, Depends

from zakupki_parser.api.app.deps import ApiContext

logger = logging.getLogger(__name__)


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
        """Сводка для вкладки «Мониторинг»: очереди каскада, наполнение индекса,
        циклы обхода площадок, ресурсы хоста.

        Все блоки — best-effort: недоступность транспорта или БД не роняет
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
            cycles = await _repo().cycle_stats_summary(kind="regular")
            db_bytes = await _repo().database_size_bytes()
        else:
            index_counts = {}
            recent_errors = []
            cycles = {"last": None, "average": None}
            db_bytes = None

        disk = psutil.disk_usage(str(Path(state.configs_dir).resolve()))
        # Файловое хранилище приложения (логи/сессия браузера/экспорты) — каталог
        # ``data/`` рядом с configs_dir (тот же принцип относительного пути, что
        # у ``browser.session_dir``/``logging.file`` по умолчанию — ``data/...``).
        data_dir = Path(state.configs_dir).resolve().parent / "data"
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
            "cycles": cycles,
            "storage": {
                "file_storage_bytes": _dir_size_bytes(data_dir),
                "db_bytes": db_bytes,
            },
            "resources": resources,
        }

    return router


def _mem_stats(mem: Any) -> dict[str, float]:
    return {"total": mem.total, "used": mem.used, "percent": mem.percent}


def _dir_size_bytes(path: Path) -> int:
    """Рекурсивный размер каталога (сумма размеров файлов), best-effort.

    Каталог может отсутствовать (свежее окружение до первого запуска парсера) —
    это не ошибка, а 0 байт. Отдельный файл может исчезнуть между обходом и
    ``stat()`` (ротация логов параллельно опросу) — пропускаем его, не роняя
    всю сводку.
    """
    if not path.exists():
        return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError as exc:
        logger.debug("Не удалось полностью обойти %s для оценки размера: %s", path, exc)
    return total
