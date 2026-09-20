"""Мониторинг фоновой индексации по ОКПД2 для devops: очереди, индекс, ресурсы хоста."""

from __future__ import annotations

import logging
import os
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
            "processes": _program_processes(),
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


# Метки процессов каскада скоринга по подстроке в cmdline — так их находит и
# scripts/run_all.sh (pgrep -f). Работает только когда воркер виден в ОДНОМ
# PID-namespace с процессом api: при локальном run_all.sh (все — отдельные
# ОС-процессы на одном хосте) видно всё; в docker-стеке (каждый сервис — свой
# контейнер/namespace) видно только сам процесс api и его потомков (см.
# _own_pids) — это не баг, а ограничение видимости процессов между контейнерами.
_PROCESS_LABEL_PATTERNS: list[tuple[str, str]] = [
    ("scoring_transport", "scoring_transport serve"),
    ("scoring_service (Fit)", "scoring_service worker"),
    ("pwin_service", "pwin_service worker"),
    ("margin_service", "margin_service worker"),
    ("analysis_service", "analysis_service"),
    ("indexing_service", "indexing_service worker"),
]

# Кэш psutil.Process по pid между опросами — нужен, чтобы cpu_percent(None)
# считал дельту с ПРЕДЫДУЩЕГО опроса (тот же принцип, что у системного
# psutil.cpu_percent(interval=None) выше), а не блокировать запрос сном.
_process_cache: dict[int, psutil.Process] = {}


def _own_pids() -> dict[int, str]:
    """PID самого API-процесса и всех его потомков (например, headless-браузер
    парсера) с метками."""
    try:
        me = psutil.Process(os.getpid())
    except psutil.Error:
        return {os.getpid(): "api (веб/цикл мониторинга)"}
    result = {me.pid: "api (веб/цикл мониторинга)"}
    try:
        for child in me.children(recursive=True):
            result[child.pid] = "api: дочерний процесс"
    except psutil.Error:
        pass
    return result


def _matched_pids() -> dict[int, str]:
    result = _own_pids()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        pid = proc.info["pid"]
        if pid in result:
            continue
        cmdline = " ".join(proc.info.get("cmdline") or [])
        for label, pattern in _PROCESS_LABEL_PATTERNS:
            if pattern in cmdline:
                result[pid] = label
                break
    return result


def _program_processes() -> list[dict[str, Any]]:
    """CPU/RAM по процессам программы — доля (%) и абсолютные значения.

    cpu_percent — как и системный (см. ``resources.cpu_percent`` выше): доля
    с МОМЕНТА ПРЕДЫДУЩЕГО опроса этого эндпоинта, а не мгновенный снимок и не
    фиксированное окно. Для процесса, впервые попавшего в выдачу, первое
    значение — заглушка 0.0 (счётчик только что «прогрет», делить пока не на
    что) — как и с системным CPU, следующий опрос уже даст реальную дельту.
    """
    matched = _matched_pids()
    total_mem = psutil.virtual_memory().total
    for pid in set(_process_cache) - set(matched):
        _process_cache.pop(pid, None)

    items: list[dict[str, Any]] = []
    for pid, label in matched.items():
        proc = _process_cache.get(pid)
        is_new = proc is None
        if is_new:
            try:
                proc = psutil.Process(pid)
                proc.cpu_percent(None)  # прогрев счётчика — само значение не используем
            except psutil.Error:
                continue
            _process_cache[pid] = proc
            cpu = 0.0
        else:
            assert proc is not None
            try:
                cpu = proc.cpu_percent(None)
            except psutil.Error:
                _process_cache.pop(pid, None)
                continue
        try:
            rss = proc.memory_info().rss
        except psutil.Error:
            _process_cache.pop(pid, None)
            continue
        items.append(
            {
                "pid": pid,
                "label": label,
                "cpu_percent": cpu,
                "rss_bytes": rss,
                "rss_percent": (rss / total_mem * 100) if total_mem else 0.0,
            }
        )
    items.sort(key=lambda x: x["rss_bytes"], reverse=True)
    return items


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
