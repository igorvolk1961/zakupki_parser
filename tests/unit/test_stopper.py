"""Unit-тесты паттернов поиска процессов для остановки (stopper)."""

from __future__ import annotations

import os
import re
from typing import Any

import pytest

from zakupki_parser.stopper import (
    _RUN_PATTERNS,
    _WIN_PATTERNS,
    _find_pids_linux,
    _find_pids_windows,
    _kill_graceful_windows,
    render_stop_failure,
)


def _any_match(cmdline: str) -> bool:
    return any(re.search(p, cmdline) for p in _RUN_PATTERNS)


def test_matches_zp_with_options() -> None:
    # Подкоманда идёт ПОСЛЕ опций (--configs).
    assert _any_match(".venv/bin/zp --configs configs serve")


def test_matches_zp_no_options() -> None:
    assert _any_match(".venv/bin/zp run-once")
    assert _any_match(".venv/bin/zp run-service")
    assert not _any_match(".venv/bin/zp score-worker")


def test_matches_alias_zakupki_parser() -> None:
    assert _any_match(".venv/bin/zakupki-parser --configs configs serve")


def test_matches_cli_py() -> None:
    assert _any_match("python3 src/zakupki_parser/cli.py run-once")


def test_matches_python_module() -> None:
    assert _any_match("python3 -m zakupki_parser.cli run-once")


def test_no_non_capturing_groups() -> None:
    # pgrep (POSIX ERE) не поддерживает (?:...) — такие группы запрещены.
    for pattern in _RUN_PATTERNS:
        assert "(?:" not in pattern


def test_does_not_match_unrelated() -> None:
    assert not _any_match("python3 server.py run-once")
    assert not _any_match("bash run-service.sh")


def _win_match(cmdline: str) -> bool:
    return any(re.search(p, cmdline) for p in _WIN_PATTERNS)


def test_win_patterns_match_serve_with_options() -> None:
    assert _win_match(".venv\\Scripts\\zp.exe --configs configs serve")


def test_win_patterns_match_subcommands() -> None:
    assert _win_match("python.exe -m zakupki_parser run-once")
    assert _win_match("python.exe -m zakupki_parser run-service")


def test_win_patterns_reject_unrelated() -> None:
    assert not _win_match("python.exe server.py")
    assert not _win_match("python.exe -m pwin_service worker")


def test_find_pids_linux_raises_without_pgrep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    try:
        _find_pids_linux(["serve"])
    except RuntimeError as exc:
        assert "pgrep" in str(exc)
    else:  # pragma: no cover - тест должен упасть без исключения
        raise AssertionError("ожидалось RuntimeError")


def test_find_pids_linux_parses_pgrep_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    calls: list[str] = []

    class _Result:
        stdout = "12\n34\n"
        returncode = 0

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Result:
        calls.append(cmd[0])
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pgrep" if name == "pgrep" else None)
    assert _find_pids_linux(["run-once"]) == [12, 34]
    assert calls == ["pgrep"]


def test_find_pids_windows_uses_powershell(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    class _Result:
        stdout = "101\n202\n"
        returncode = 0

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Result:
        assert cmd[0] == "powershell"
        assert "-Command" in cmd
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert _find_pids_windows(["serve"]) == [101, 202]


def test_find_pids_windows_ignores_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    class _Result:
        stdout = "7\nnot-a-pid\n99\n"
        returncode = 0

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Result:
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert _find_pids_windows(["serve"]) == [7, 99]


def test_kill_graceful_windows_uses_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    calls: list[list[str]] = []

    class _Ok:
        returncode = 0

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Ok:
        calls.append(cmd)
        return _Ok()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("zakupki_parser.stopper._alive", lambda pid: False)
    remaining = _kill_graceful_windows([11, 22], force=True)
    assert remaining == []
    # По каждому PID: taskkill /F /PID + taskkill /F /T /PID.
    assert len(calls) == 4
    assert calls[0][0] == "taskkill"
    assert "/F" in calls[0]
    assert calls[0][-1] == "11"


def test_kill_graceful_windows_reports_remaining(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    calls: list[list[str]] = []

    class _Fail:
        returncode = 1

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Fail:
        calls.append(cmd)
        return _Fail()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    # Процесс считается живым после неудачной taskkill.
    monkeypatch.setattr("zakupki_parser.stopper._alive", lambda pid: True)
    remaining = _kill_graceful_windows([5], force=True)
    assert remaining == [5]


def _expect_uid(pid: int) -> int:
    return 0 if pid == 1 else 1000


def test_render_stop_failure_empty() -> None:
    assert render_stop_failure([]) == ""


def test_render_stop_failure_docker_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zakupki_parser.stopper._in_docker", lambda pid: True)
    monkeypatch.setattr("zakupki_parser.stopper._pid_uid", _expect_uid)
    msg = render_stop_failure([560686, 560687])
    assert "Docker" in msg
    assert "compose.sh stop" in msg


def test_render_stop_failure_foreign_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zakupki_parser.stopper._in_docker", lambda pid: False)
    monkeypatch.setattr("zakupki_parser.stopper._pid_uid", lambda pid: 0)
    msg = render_stop_failure([42])
    assert "другому пользователю" in msg
    assert "sudo" in msg


def test_render_stop_failure_stuck(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zakupki_parser.stopper._in_docker", lambda pid: False)
    monkeypatch.setattr("zakupki_parser.stopper._pid_uid", lambda pid: os.getuid())
    msg = render_stop_failure([7])
    assert "--force" in msg


def test_render_stop_failure_dedups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zakupki_parser.stopper._in_docker", lambda pid: False)
    monkeypatch.setattr("zakupki_parser.stopper._pid_uid", lambda pid: os.getuid())
    msg = render_stop_failure([7, 7])
    assert msg.count("7") == 1
