"""Остановка запущенных процессов парсера.

Ищет процессы команд ``run-once`` / ``run-service`` / ``serve``
по командной строке, останавливает их мягко (SIGINT — корректное закрытие браузера),
при необходимости доводя SIGTERM/SIGKILL. Также добивает осиротевшие
Playwright-драйвер и headless-Chromium, запущенные парсером.

Кроссплатформенно:
- Linux — ``pgrep`` (procps), сигналы через ``os.kill``;
- Windows — поиск через PowerShell ``Get-CimInstance Win32_Process`` по командной
  строке, остановка через ``taskkill`` (мягко/принудительно).
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
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

# Паттерны для поиска через PowerShell (Windows): подкоманда идёт после опций.
# \b — границы слов, чтобы "serve" не совпадал с "server.py".
_WIN_PATTERNS = [
    r"\b(run-once|run-service|serve)\b",
]

# Альтернативные имена python-исполняемых файлов (запуск через uv-обёртки).
_PYTHON_NAMES = ("python.exe", "python", "uv.exe", "uv")


def _on_windows() -> bool:
    return sys.platform == "win32"


def _find_pids_linux(patterns: list[str]) -> list[int]:
    if shutil.which("pgrep") is None:
        raise RuntimeError(
            "pgrep не найден в PATH — остановка парсера невозможна. "
            "Установите procps (Linux) или используйте pkill напрямую."
        )
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


def _find_pids_windows(patterns: list[str]) -> list[int]:
    """Поиск python/uv-процессов по командной строке через PowerShell.

    Ищем процессы, чья CommandLine содержит подкоманду парсера и путь/имя
    пакета ``zakupki_parser`` (отсекает чужие python-процессы). Ограничение по
    имени процесса не делаем: запуск идёт и через ``python.exe``, и через
    uv-обёртки.
    """
    name_filter = " -or ".join(f"$_.Name -eq '{name}'" for name in _PYTHON_NAMES)
    pattern_or = " -or ".join(f"($_.CommandLine -match '{p}')" for p in patterns)
    script = (
        "Get-CimInstance Win32_Process "
        f"| Where-Object {{ ({name_filter}) -and ({pattern_or}) "
        "-and ($_.CommandLine -match 'zakupki_parser') } "
        "| ForEach-Object { $_.ProcessId }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        if line.strip().isdigit():
            pids.append(int(line.strip()))
    return list(dict.fromkeys(pids))


def _pids_for(patterns: list[str]) -> list[int]:
    if _on_windows():
        return _find_pids_windows(_WIN_PATTERNS)
    return _find_pids_linux(patterns)


def _alive(pid: int) -> bool:
    if _on_windows():
        # os.kill(pid, 0) на Windows для чужих процессов бросает SystemError —
        # используем tasklist (нативный способ проверки существования процесса).
        # Вывод tasklist идёт в OEM-кодировке (cp866): text=True без errors
        # падает на декодировании (UnicodeDecodeError) и stdout становится None.
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        return str(pid) in (result.stdout or "")
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


def _pid_uid(pid: int) -> int | None:
    """UID владельца процесса (None, если прочитать не удалось)."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _container_id(pid: int) -> str | None:
    """Docker container ID из cgroup процесса (None, если это не Docker).

    Матчит и cgroup v1 (``.../docker/<id>``), и cgroup v2
    (``.../docker-<id>.scope``).
    """
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
            cgroup = f.read()
    except OSError:
        return None
    match = re.search(r"docker-([0-9a-f]{12,64})\.scope", cgroup)
    if match:
        return match.group(1)
    match = re.search(r"docker/([0-9a-f]{12,64})", cgroup)
    if match:
        return match.group(1)
    return None


def _in_docker(pid: int) -> bool:
    """True, если процесс живёт в cgroup Docker-контейнера."""
    return _container_id(pid) is not None


def _compose_project_of(container_id: str) -> str | None:
    """Compose-проект контейнера (label com.docker.compose.project), либо None.

    Возвращает None, если docker недоступен, контейнер не найден или он
    не управляется docker compose (например, поднят через db_up.sh).
    """
    if shutil.which("docker") is None:
        return None
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--no-trunc",
                "--filter",
                f"id={container_id}",
                "--format",
                '{{.Label "com.docker.compose.project"}}',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except OSError:
        return None
    project = (result.stdout or "").strip()
    return project or None


def _compose_stop_command(project: str) -> str:
    """Команда остановки compose-стека для данного проекта."""
    if project == "zakupki-demo":
        return "scripts/compose.sh demo stop"
    return "scripts/compose.sh stop"


def _docker_failure_hint(pids: list[int]) -> str:
    """Подсказка для процессов в Docker-контейнерах с учётом compose-проекта.

    Группирует процессы по проекту и подбирает корректную команду остановки
    (dev-стек ``scripts/compose.sh stop``, изолированный демо —
    ``scripts/compose.sh demo stop``).
    """
    by_project: dict[str, list[int]] = {}
    unknown: list[int] = []
    project_cache: dict[str, str | None] = {}
    for pid in pids:
        container_id = _container_id(pid)
        if container_id is None:
            unknown.append(pid)
            continue
        if container_id not in project_cache:
            project_cache[container_id] = _compose_project_of(container_id)
        project = project_cache[container_id]
        if project:
            by_project.setdefault(project, []).append(pid)
        else:
            unknown.append(pid)

    hints: list[str] = []
    for project, ids in by_project.items():
        ids_str = ", ".join(map(str, ids))
        hints.append(
            f"процессы {ids_str} идут в контейнерах Docker под root "
            f"(проект {project}) и не останавливаются сигналом с хоста. "
            "Остановите стек: "
            f"`{_compose_stop_command(project)}` (или `docker stop <контейнер>`)."
        )
    if unknown:
        ids_str = ", ".join(map(str, unknown))
        hints.append(
            f"процессы {ids_str} идут в контейнерах Docker под root и не "
            "останавливаются сигналом с хоста. "
            "Остановите их: `docker stop <контейнер>`."
        )
    return " ".join(hints)


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
    if _on_windows():
        return _kill_graceful_windows(pids, force)
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


def _kill_graceful_windows(pids: list[int], force: int | bool) -> list[int]:
    """Windows: taskkill (мягко — без /F, принудительно — с /F)."""
    args = ["taskkill"]
    if force:
        args.append("/F")
    remaining: list[int] = []
    for pid in pids:
        result = subprocess.run(
            [*args, "/PID", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        # taskkill /T — добиваем дочерние процессы (браузер и т.п.).
        subprocess.run(
            [*args, "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if result.returncode != 0 and _alive(pid):
            remaining.append(pid)
    return remaining


def stop_parser(force: bool = False) -> list[int]:
    """Останавливает парсер и его браузерные процессы.

    Возвращает список PIDs, которые не удалось завершить (пусто — успех).
    """
    target = _pids_for(_RUN_PATTERNS)
    leftover = _pids_for(_LEFTOVER_PATTERNS)
    remaining: list[int] = []
    if target:
        remaining += _kill_graceful(target, force)
    if leftover:
        # Браузерные процессы добиваем без мягкой стадии.
        remaining += _kill_graceful(leftover, True)
    return list(dict.fromkeys(remaining))


def render_stop_failure(remaining: list[int]) -> str:
    """Собирает понятное сообщение о неостановленных процессах.

    Различает три случая:
    - процессы в Docker-контейнерах (root в отдельном user namespace) — их нельзя
      остановить сигналом с хоста, нужен ``docker stop`` / ``scripts/compose.sh stop``
      (для изолированного демо — ``scripts/compose.sh demo stop``);
    - процессы другого пользователя — нет прав на сигнал;
    - обычные процессы, которые просто не завершились — стоит попробовать --force.
    """
    pids = list(dict.fromkeys(remaining))
    if not pids:
        return ""
    if _on_windows():
        # Анализ по UID/cgroup (docker, чужой пользователь) работает только на Linux:
        # taskkill-путь на Windows не различает такие случаи.
        return (
            "Не удалось остановить: процессы "
            + ", ".join(map(str, pids))
            + " не завершились — попробуйте `zp stop --force`."
        )
    docker = [p for p in pids if _in_docker(p)]
    foreign = [p for p in pids if p not in docker and _pid_uid(p) not in (None, os.getuid())]
    stuck = [p for p in pids if p not in docker and p not in foreign]

    parts: list[str] = []
    if docker:
        parts.append(_docker_failure_hint(docker))
    if foreign:
        parts.append(
            f"процессы {', '.join(map(str, foreign))} принадлежат другому пользователю — "
            "нет прав на сигнал, остановите их от его имени (sudo)."
        )
    if stuck:
        parts.append(
            f"процессы {', '.join(map(str, stuck))} не завершились — попробуйте `zp stop --force`."
        )
    return "Не удалось остановить: " + " ".join(parts)
