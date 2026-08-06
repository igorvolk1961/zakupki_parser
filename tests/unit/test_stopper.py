"""Unit-тесты паттернов поиска процессов для остановки (stopper)."""

from __future__ import annotations

import re

from zakupki_parser.stopper import _RUN_PATTERNS


def _any_match(cmdline: str) -> bool:
    return any(re.search(p, cmdline) for p in _RUN_PATTERNS)


def test_matches_zp_with_options() -> None:
    # Подкоманда идёт ПОСЛЕ опций (--configs).
    assert _any_match(".venv/bin/zp --configs configs serve")


def test_matches_zp_no_options() -> None:
    assert _any_match(".venv/bin/zp run-once")
    assert _any_match(".venv/bin/zp run-service")
    assert _any_match(".venv/bin/zp score-worker")


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
