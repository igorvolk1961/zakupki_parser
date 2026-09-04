"""Рестарт фоновых сервисов скоринга из web-интерфейса (вариант A).

Фоновые сервисы (``scoring_service``, ``analysis_service``, ``pwin_service``,
``margin_service``) запускаются как отдельные ОС-процессы (см. scripts/run_all.sh).
Рестарт здесь означает: найти рабочие процессы по командной строке, завершить их и
поднять сервис заново той же командой. Сервисы читают секреты из собственного
``.env`` и запускаются как ``uv run python -m <module> worker`` — как в run_all.sh.

Модуль не зависит от ``routes``/``state`` (работает с примитивами), чтобы логика
поиска/завершения/запуска проверялась юнит-тестами без реального поднятия сервисов.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Команда запуска воркера: ``uv run python -m <module> <cmd>`` (как в run_all.sh).
LAUNCH_WRAPPER = ("uv", "run", "python", "-m")


def _pgrep(pattern: str) -> list[int]:
    """PID процессов, чья командная строка содержит ``pattern`` (Linux-only)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        # pgrep недоступен (например, Windows) — управление процессами не работает.
        return []
    return [int(token) for token in result.stdout.split() if token.strip().isdigit()]


def find_worker_pids(module: str, cmd: str) -> list[int]:
    """PID воркеров сервиса: сам python-процесс (``python -m <module> <cmd>``)
    и его uv-обёртка (``uv run python -m <module> <cmd>``). Обе строки матчатся
    ``pgrep -f``, поэтому отдельный паттерн для общего вида не нужен."""
    pids: set[int] = set()
    for pattern in (f"python -m {module} {cmd}", f"{module} {cmd}"):
        pids.update(_pgrep(pattern))
    return sorted(pids)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _signal(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def terminate_pids(pids: list[int], grace: float = 5.0) -> int:
    """Корректно завершает процессы (SIGTERM, затем SIGKILL по таймауту).

    Возвращает число процессов, на которые отдан сигнал завершения.
    """
    if not pids:
        return 0
    for pid in pids:
        _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    running = {pid for pid in pids if _alive(pid)}
    while running and time.monotonic() < deadline:
        running = {pid for pid in running if _alive(pid)}
        if running:
            time.sleep(0.1)
    for pid in list(running):
        _signal(pid, signal.SIGKILL)
    return len(pids)


def _build_pythonpath(project_root: Path, existing: str | None) -> str:
    """PYTHONPATH для воркеров: общие компоненты каскада + прежний PYTHONPATH.

    ``scoring_common`` локально не установлен как пакет, поэтому его каталог
    добавляется в PYTHONPATH (как в run_all.sh).
    """
    common = str((project_root / "src" / "scoring_common").resolve())
    if existing:
        return common + os.pathsep + existing
    return common


def launch_worker(
    *,
    project_root: Path,
    service_dir: str,
    module: str,
    cmd: str,
    parser_env: str,
    parser_url: str,
    log_path: Path,
    pythonpath: str = "",
) -> int:
    """Запускает воркер сервиса как фоновый (detached) процесс; возвращает его PID.

    Лог пишется в ``log_path`` (в режиме дозаписи, чтобы не стирать предыдущий
    прогон). Секреты сервис читает из собственного ``.env`` и переменных окружения.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = _build_pythonpath(project_root, pythonpath)
    env[parser_env] = parser_url
    with open(log_path, "a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [*LAUNCH_WRAPPER, module, cmd],
            cwd=str((project_root / "src" / service_dir).resolve()),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    logger.info("Запущен воркер %s (PID %s), лог: %s", module, proc.pid, log_path)
    return proc.pid
