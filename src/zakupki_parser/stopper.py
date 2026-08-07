"""Остановка запущенных процессов парсера.

Ищет процессы команд ``run-once`` / ``run-service`` / ``serve``
по командной строке, останавливает их мягко (SIGINT — корректное закрытие браузера),
при необходимости доводя SIGTERM/SIGKILL. Также добивает осиротевшие
Playwright-драйвер и headless-Chromium, запущенные парсером.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time

# Раздельные паттерны (pgrep не поддерживает (?:...); подкоманда идёт ПОСЛЕ
# возможных опций, напр. "bin/zp --configs configs serve").
_RUN_PATTERNS = [
    r"cli\.py (run-once|run-service|serve)",
    r"zakupki_parser\.cli (run-once|run-service|serve)",
    r"bin/zp .*(run-once|run-service|serve)",
    r"bin/zakupki-parser .*(run-once|run-service|serve)",
]

# Осиротевшие браузерные дочерние процессы (если основной процесс уже умер).
_LEFTOVER_PATTERNS = [
    r"playwright/driver/.+run-driver",
    r"chrome.+--user-data-dir=/tmp/playwright",
]


def _require_pgrep() -> None:
    """Бросает RuntimeError, если pgrep недоступен в системе."""
    if shutil.which("pgrep") is None:
        raise RuntimeError(
            "pgrep не найден в PATH — остановка парсера невозможна. "
            "Установите procps (Linux) или используйте pkill напрямую."
        )


def _pids_for(patterns: list[str]) -> list[int]:
    pids: list[int] = []
    for pattern in patterns:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            if line.strip().isdigit():
                pids.append(int(line.strip()))
    return list(dict.fromkeys(pids))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _signal(pid: int, sig: signal.Signals) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _drain(pid: int, wait: float) -> bool:
    """Ждёт завершения процесса; возвращает False, если процесс ещё жив."""
    if not _alive(pid):
        return True
    time.sleep(wait)
    return not _alive(pid)


def _kill_graceful(pids: list[int], force: bool) -> list[int]:
    """Останавливает процессы; возвращает список всё ещё живых PIDs."""
    if not pids:
        return []
    if force:
        for pid in pids:
            _signal(pid, signal.SIGKILL)
        time.sleep(0.5)
        return [p for p in pids if _alive(p)]
    for pid in pids:
        _signal(pid, signal.SIGINT)
    alive = [p for p in pids if not _drain(p, 2.0)]
    for pid in alive:
        _signal(pid, signal.SIGTERM)
    alive = [p for p in alive if not _drain(p, 1.0)]
    for pid in alive:
        _signal(pid, signal.SIGKILL)
    time.sleep(0.5)
    return [p for p in alive if _alive(p)]


def stop_parser(force: bool = False) -> list[int]:
    """Останавливает парсер и его браузерные процессы.

    Возвращает список PIDs, которые не удалось завершить (пусто — успех).
    """
    _require_pgrep()
    target = _pids_for(_RUN_PATTERNS)
    leftover = _pids_for(_LEFTOVER_PATTERNS)
    remaining: list[int] = []
    if target:
        remaining += _kill_graceful(target, force)
    if leftover:
        # Браузерные процессы добиваем без мягкой стадии.
        remaining += _kill_graceful(leftover, True)
    return list(dict.fromkeys(remaining))
