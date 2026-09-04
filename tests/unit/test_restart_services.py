"""Тесты рестарта фоновых сервисов (вариант A: subprocess).

Проверяются чистые функции модуля restart_services без реального поиска/завершения
процессов (os.kill/subprocess.Popen подменяются фикстурами).
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

import pytest

from zakupki_parser.api.app.restart_services import (
    _build_pythonpath,
    _pgrep,
    find_worker_pids,
    launch_worker,
    terminate_pids,
)


def test_build_pythonpath_adds_common(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    value = _build_pythonpath(root, existing=None)
    assert value == str((root / "src" / "scoring_common").resolve())


def test_build_pythonpath_preserves_existing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    value = _build_pythonpath(root, existing="/old/path")
    assert value == str((root / "src" / "scoring_common").resolve()) + os.pathsep + "/old/path"


def test_find_worker_pids_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    results = {
        "python -m scoring_service worker": [10, 20],
        "scoring_service worker": [20, 30],
    }

    def fake_pgrep(pattern: str) -> list[int]:
        return results[pattern]

    monkeypatch.setattr("zakupki_parser.api.app.restart_services._pgrep", fake_pgrep)
    assert find_worker_pids("scoring_service", "worker") == [10, 20, 30]


def test_pgrep_returns_empty_when_command_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если pgrep отсутствует (FileNotFoundError) — возвращаем пустой список."""

    def raise_missing(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("pgrep not found")

    monkeypatch.setattr("zakupki_parser.api.app.restart_services.subprocess.run", raise_missing)
    assert _pgrep("any-pattern") == []


def test_terminate_pids_sends_term(monkeypatch: pytest.MonkeyPatch) -> None:
    """Процессы завершаются сразу: _alive False, значит только SIGTERM."""
    sent_sigs: list[tuple[int, int]] = []

    def record_signal(pid: int, sig: int) -> bool:
        sent_sigs.append((pid, sig))
        return True

    monkeypatch.setattr("zakupki_parser.api.app.restart_services._alive", lambda pid: False)
    monkeypatch.setattr("zakupki_parser.api.app.restart_services._signal", record_signal)
    assert terminate_pids([101, 102]) == 2
    assert sent_sigs == [(101, signal.SIGTERM), (102, signal.SIGTERM)]


def test_terminate_pids_no_pids() -> None:
    assert terminate_pids([]) == 0


def test_launch_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Проверяем команду, окружение и лог без реального запуска процесса."""
    captured: dict[str, Any] = {}

    class FakeProc:
        pid = 4242

    def fake_popen(argv: list[str], **kwargs: Any) -> FakeProc:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        "zakupki_parser.api.app.restart_services._build_pythonpath",
        lambda root, existing: "SRC_COMMON",
    )

    root = tmp_path / "repo"
    log_path = tmp_path / "logs" / "scoring_service.log"
    pid = launch_worker(
        project_root=root,
        service_dir="scoring_service",
        module="scoring_service",
        cmd="worker",
        parser_env="SCORE_PARSER_API_URL",
        parser_url="http://127.0.0.1:8000",
        log_path=log_path,
    )
    assert pid == 4242
    assert captured["argv"] == ["uv", "run", "python", "-m", "scoring_service", "worker"]
    assert str(captured["kwargs"]["cwd"]).endswith("/scoring_service")
    assert captured["kwargs"]["env"]["PYTHONPATH"] == "SRC_COMMON"
    assert captured["kwargs"]["env"]["SCORE_PARSER_API_URL"] == "http://127.0.0.1:8000"
    assert captured["kwargs"]["start_new_session"] is True
    assert log_path.is_file()
