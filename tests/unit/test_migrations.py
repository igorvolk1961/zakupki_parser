"""Unit-тесты автоматического применения Liquibase-миграций."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from zakupki_parser.migrations import (
    LIQUIBASE_IMAGE,
    _liquibase_command,
    changelog_dir,
    jdbc_from_dsn,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestJdbcFromDsn:
    def test_with_credentials(self) -> None:
        jdbc, user, password = jdbc_from_dsn(
            "postgresql+asyncpg://postgres:secret@localhost:5432/zakupki"
        )
        assert jdbc == "jdbc:postgresql://localhost:5432/zakupki"
        assert user == "postgres"
        assert password == "secret"

    def test_scheme_normalized(self) -> None:
        jdbc, _, _ = jdbc_from_dsn("postgresql://u:p@db/z")
        assert jdbc == "jdbc:postgresql://db/z"

    def test_without_credentials(self) -> None:
        jdbc, user, password = jdbc_from_dsn("postgresql://localhost:5432/zakupki")
        assert jdbc == "jdbc:postgresql://localhost:5432/zakupki"
        assert user == ""
        assert password == ""


def test_changelog_dir_resolves_relative_to_repo() -> None:
    expected = REPO_ROOT / "docker" / "liquibase" / "changelog"
    assert changelog_dir(REPO_ROOT / "configs") == expected


def test_liquibase_command_prefers_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def _which(name: str) -> str | None:
        return "/usr/bin/liquibase" if name == "liquibase" else None

    monkeypatch.setattr(shutil, "which", _which)
    cmd = _liquibase_command(Path("/changelog"), {"K": "V"})
    assert cmd is not None
    assert cmd[0] == "liquibase"
    assert "update" in cmd
    assert "K=V" not in cmd  # CLI получает env, не аргументами


def test_liquibase_command_falls_back_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    cmd = _liquibase_command(Path("/changelog"), {"LIQUIBASE_COMMAND_URL": "jdbc:postgresql://h/d"})
    assert cmd is not None
    assert cmd[0] == "docker"
    assert "run" in cmd
    assert "-e" in cmd
    assert "LIQUIBASE_COMMAND_URL=jdbc:postgresql://h/d" in cmd
    assert LIQUIBASE_IMAGE in cmd


def test_liquibase_command_none_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _liquibase_command(Path("/changelog"), {}) is None
