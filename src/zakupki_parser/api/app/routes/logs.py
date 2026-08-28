"""Эндпоинты просмотра логов (вкладка «Логи», devops).

Хвост файла лога (``config_log.yaml -> file``) с фильтрацией по уровню
(ошибки/предупреждения), текстовому поиску и диапазону дат. Автообновление —
на стороне клиента (периодический запрос с теми же параметрами).

Безопасность: файл лога читается только внутри корня проекта (путём задаётся
относительно корня и проверяется на выход за него); при тайл-чтении не читается
весь файл.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from zakupki_parser.api.app.deps import ApiContext

logger = logging.getLogger(__name__)

# Токен уровня лога в первых символах строки (формат "%(levelname)-8s").
_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")

# Максимум строк в ответе (защита от гигантских файлов при фильтрации).
_MAX_LINES = 2000

# Временная зона, в которой пишется лог (локальная зона сервера): метки времени
# строк лога интерпретируем как локальные, чтобы корректно сравнивать с
# фильтрами дат (браузер присылает ISO-строки с часовым поясом).
_LOCAL_TZ = datetime.now().astimezone().tzinfo

# Относительные пути логов, доступных к просмотру (относительно корня проекта):
# основной лог парсера (config_log.yaml -> file) и файлы фоновых сервисов
# (data/logs/*.log). Список строится динамически (см. _list_log_files).
_LOG_DIR_REL = "data/logs"


def _log_root(state: Any) -> Path:
    """Корень проекта: resolved-каталог конфигов + «вверх» (configs/..)."""
    return Path(state.cfg.configs_dir).resolve().parent


def _rel_to_root(root: Path, path: Path) -> str | None:
    """Относительный путь от корня (или None, если файл вне корня)."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def _list_log_files(state: Any) -> list[dict[str, str]]:
    """Доступные файлы логов: основной лог парсера + все data/logs/*.log.

    Возвращает относительные пути (``rel``) — их принимает ``/api/logs/tail``,
    и человекочитаемые метки (``label``) для селектора во вкладке «Логи».
    """
    root = _log_root(state)
    seen: set[str] = set()
    files: list[dict[str, str]] = []

    def add(path: Path) -> None:
        rel = _rel_to_root(root, path)
        if rel is None or rel in seen:
            return
        seen.add(rel)
        files.append({"rel": rel, "label": path.name, "path": str(path)})

    main_file = state.cfg.logging.file
    if main_file:
        add(Path(main_file).resolve())
    logs_dir = root / _LOG_DIR_REL
    if logs_dir.is_dir():
        for p in sorted(logs_dir.iterdir(), key=lambda x: x.name):
            if p.suffix == ".log" and p.is_file():
                add(p)
    return files


def _parse_ts(line: str) -> datetime | None:
    """Время из префикса строки (asctime: 'YYYY-MM-DD HH:MM:SS'), локальное."""
    if len(line) < 19:
        return None
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_LOCAL_TZ)
    except ValueError:
        return None


def _normalize_dt(value: datetime | None) -> datetime | None:
    """Приводит фильтр дат к осознанному времени (наивные строки — в локальную зону)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_LOCAL_TZ)
    return value


def _line_level(line: str) -> str | None:
    m = _LEVEL_RE.search(line[:40])
    return m.group(1) if m else None


def _read_tail(path: Path, lines: int) -> list[str]:
    """Последние ``lines`` строк файла (без чтения всего файла целиком)."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    max_bytes = max(lines * 512, 65536)
    with path.open("rb") as f:
        if size <= max_bytes:
            f.seek(0)
            data = f.read()
        else:
            f.seek(size - max_bytes)
            data = f.read()
    text = data.decode("utf-8", errors="replace")
    if not text:
        return []
    if not text.endswith("\n"):
        text += "\n"
    parts = text.splitlines()
    # Первый элемент буфера при усечении — частичная строка (начали с середины строки).
    if size > max_bytes and parts:
        parts = parts[1:]
    return parts[-lines:]


def build_logs_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    require_devops = ctx.require_devops

    @router.get(
        "/api/logs/files",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def logs_files() -> dict[str, object]:
        """Список файлов логов для селектора во вкладке «Логи» (devops)."""
        files = _list_log_files(state)
        return {"files": files, "root": str(_log_root(state))}

    @router.get(
        "/api/logs/tail",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def logs_tail(
        lines: int = Query(default=200, ge=10, le=2000),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        level: Literal["all", "error", "warning"] = Query(default="all"),
        q: str | None = Query(default=None),
        file: str | None = Query(default=None),
    ) -> dict[str, object]:
        """Хвост лога с фильтрами (уровень, поиск, диапазон дат).

        ``file`` — относительный путь (из ``/api/logs/files``) к файлу лога;
        без него берётся основной лог (``config_log.yaml -> file``).
        """
        root = Path(state.cfg.configs_dir).resolve().parent
        if file:
            path = (root / file).resolve()
        else:
            raw_path = state.cfg.logging.file
            if raw_path is None:
                return {
                    "path": None,
                    "file_exists": False,
                    "lines": [],
                    "count": 0,
                    "truncated": False,
                }
            path = Path(raw_path).resolve()
        # Файл лога читается только внутри корня проекта. Корень — resolved-каталог
        # конфигов (cfg.configs_dir), а не сырой аргумент: последний может быть
        # относительным и зависеть от рабочей директории процесса.
        if not path.is_relative_to(root):
            raise HTTPException(status_code=403, detail="Путь файла лога вне корня проекта")
        if not path.is_file():
            return {
                "path": str(path),
                "file_exists": False,
                "lines": [],
                "count": 0,
                "truncated": False,
            }

        try:
            from_dt = _normalize_dt(datetime.fromisoformat(from_) if from_ else None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Некорректный параметр from") from exc
        try:
            to_dt = _normalize_dt(datetime.fromisoformat(to) if to else None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Некорректный параметр to") from exc

        # Без диапазона дат читаем хвост (ограниченный объём); с диапазоном — весь
        # файл (строки старше окна тайл-буфера могут попасть в фильтр).
        if from_dt is None and to_dt is None:
            raw_lines = _read_tail(path, lines)
        else:
            try:
                raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.error("Не удалось прочитать лог %s: %s", path, exc)
                raise HTTPException(
                    status_code=500, detail=f"Не удалось прочитать лог: {exc}"
                ) from exc

        wanted_levels: set[str] | None = None
        if level == "error":
            wanted_levels = {"ERROR", "CRITICAL"}
        elif level == "warning":
            wanted_levels = {"WARNING"}

        result: list[str] = []
        for line in raw_lines:
            line_level = _line_level(line)
            if wanted_levels is not None and line_level not in wanted_levels:
                continue
            if q and q.lower() not in line.lower():
                continue
            ts = _parse_ts(line)
            if from_dt is not None or to_dt is not None:
                if ts is None:
                    continue
                if from_dt is not None and ts < from_dt:
                    continue
                if to_dt is not None and ts > to_dt:
                    continue
            result.append(line)

        truncated = len(result) > _MAX_LINES
        result = result[-_MAX_LINES:]
        return {
            "path": str(path),
            "file_exists": True,
            "lines": result,
            "count": len(result),
            "truncated": truncated,
        }

    return router
