"""Тесты конфигурации сервиса: YAML-файл + переопределение env."""

from __future__ import annotations

from pathlib import Path

import pytest

from scoring_service.settings import Settings


def test_defaults_used_when_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCORE_CONFIG_FILE", raising=False)
    monkeypatch.chdir(tmp_path)  # в пустой папке нет config.yaml/.env
    s = Settings()
    assert s.llm_model == "gpt-4o-mini"
    assert s.score_use_stub is False


def test_yaml_config_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "llm_model: my-model\nscore_use_stub: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCORE_CONFIG_FILE", str(cfg))
    monkeypatch.chdir(tmp_path)  # изолируем от локального .env в каталоге подпроекта
    s = Settings()
    assert s.llm_model == "my-model"
    assert s.score_use_stub is True
    # Не переопределённое — дефолт.
    assert s.llm_base_url == "https://api.openai.com/v1"


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("llm_model: yaml-model\n", encoding="utf-8")
    monkeypatch.setenv("SCORE_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("SCORE_LLM_MODEL", "env-model")
    monkeypatch.chdir(tmp_path)  # изолируем от локального .env в каталоге подпроекта
    s = Settings()
    assert s.llm_model == "env-model"


def test_env_overrides_yaml_bool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SCORE_USE_STUB из env должен переопределять bool из yaml (регрессия AliasChoices)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("score_use_stub: true\n", encoding="utf-8")
    monkeypatch.setenv("SCORE_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("SCORE_USE_STUB", "false")
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.score_use_stub is False


def test_dotenv_overrides_yaml_bool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SCORE_USE_STUB=false из .env должен переопределять bool из yaml."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("score_use_stub: true\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text('SCORE_USE_STUB="false"\n', encoding="utf-8")
    monkeypatch.setenv("SCORE_CONFIG_FILE", str(cfg))
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.score_use_stub is False
