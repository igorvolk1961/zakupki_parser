"""Мониторинг фоновой индексации по ОКПД2 для devops: очереди, индекс, ресурсы хоста."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from zakupki_parser.api.app.deps import ApiContext

logger = logging.getLogger(__name__)


class IndexRetryOut(BaseModel):
    procurement_id: int
    requeued: bool


def build_monitoring_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    require_analyst_or_devops = ctx.require_analyst_or_devops

    @router.get(
        "/api/devops/monitoring",
        include_in_schema=False,
        # require_analyst_or_devops (не только devops): вкладка теперь видна и
        # аналитику (Dead Letter Queue фоновой индексации, ниже) — ничего из
        # состава ответа (глубина очередей/ресурсы хоста/циклы обхода) не
        # чувствительно, поэтому отдельный урезанный ответ для аналитика не нужен.
        dependencies=[Depends(require_analyst_or_devops)],
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

    @router.get(
        "/api/devops/index-dead-letter",
        include_in_schema=False,
        dependencies=[Depends(require_analyst_or_devops)],
    )
    async def index_dead_letter() -> dict[str, Any]:
        """Dead Letter Queue фоновой индексации: записи, исчерпавшие ``max_attempts``.

        Путь под ``/api/devops/...`` сохранён по аналогии с остальной вкладкой
        «Мониторинг», но доступ — аналитику ИЛИ devops (``require_analyst_or_
        devops``), а не только devops: аналитик разбирает причины (обычно битые/
        недоступные файлы площадки), devops — эксплуатационная сторона.
        """
        if state.repository is None:
            return {"entries": []}
        return {"entries": await _repo().dead_letter_index_entries()}

    @router.post(
        "/api/devops/index-dead-letter/{procurement_id}/retry",
        response_model=IndexRetryOut,
        include_in_schema=False,
        dependencies=[Depends(require_analyst_or_devops)],
    )
    async def retry_index_dead_letter(procurement_id: int) -> IndexRetryOut:
        """Ручной повтор одной dead-letter записи: сброс attempts/status='pending' +
        немедленная повторная постановка задания индексации.

        ``_enqueue_index_job`` (обычный индексный обход) ставит задание индексации
        только ОДИН раз — при первом сохранении закупки, поэтому сам по себе сброс
        статуса в БД ничего не переиндексирует: закупка уже известна площадке и
        обычный обход её больше не трогает. Recovery-проход
        (``Scheduler._recover_index_queue``) тоже не подхватит — он смотрит только
        ``status='error'``, а не ``'pending'``. Поэтому здесь enqueue делается явно.
        """
        ok = await _repo().reset_index_entry_for_retry(procurement_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Запись индекса не найдена")
        if state.score_transport is not None:
            try:
                await state.score_transport.enqueue(
                    procurement_id, datetime.now(UTC).timestamp(), stage="index", profile_id=0
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Не удалось поставить повторное задание индексации закупки %s: %s",
                    procurement_id,
                    exc,
                )
        return IndexRetryOut(procurement_id=procurement_id, requeued=True)

    @router.get(
        "/api/devops/platform-stats",
        include_in_schema=False,
        dependencies=[Depends(require_analyst_or_devops)],
    )
    async def platform_stats(
        search: str | None = None,
        only_failed: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Статистика обхода ПО ПЛОЩАДКАМ, отдельно от сводки цикла целиком
        (``cycles`` в ``monitoring()``): последняя обработка + накопленное среднее
        по каждой площадке, с поиском и пагинацией — рассчитано на рост числа
        площадок далеко за текущие 10 (см. докстринг ``ParserPlatformStats``).
        """
        if state.repository is None:
            return {"total": 0, "items": []}
        rows, total = await _repo().list_platform_stats(
            search=search, only_failed=only_failed, limit=limit, offset=offset
        )
        return {
            "total": total,
            "items": [_repo().platform_stats_out(r) for r in rows],
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
