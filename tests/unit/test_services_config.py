"""Тесты конфигурации фоновых сервисов (вкладка «Сервисы», devops).

Только логика без БД: модели сервисов (+ секреты не выводятся), стриппинг секретов,
проверка синтаксиса .env и резолвер путей к config.yaml/.env сервиса.
"""

from __future__ import annotations

import types
from typing import Any, cast

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from zakupki_parser.api.app.routes.config import (
    SERVICE_CONFIGS,
    _service_paths,
    _service_status,
    _start_service,
    _stop_service,
    _strip_secrets,
    _validate_env_content,
)
from zakupki_parser.config.models import (
    AnalysisServiceConfig,
    MarginServiceConfig,
    PwinServiceConfig,
    ScoringServiceConfig,
)


def test_service_models_contain_no_secret_fields_and_roundtrip() -> None:
    """Модели сервисов: несекретные поля, секреты не выводятся в форму."""
    for model in (
        ScoringServiceConfig,
        AnalysisServiceConfig,
        MarginServiceConfig,
        PwinServiceConfig,
    ):
        # Поля модели-образца — только несекретные (можно просто провалидировать).
        data = model().model_dump()
        assert isinstance(data, dict)
        assert not (set(data) & {"llm_api_key", "auth_token", "parser_internal_token"})


def test_scoring_service_schema_has_no_secrets() -> None:
    from zakupki_parser.api.app.config_schema import build_schema

    schema = build_schema(ScoringServiceConfig)
    keys = {f["key"] for f in schema}
    assert {
        "llm_base_url",
        "llm_model",
        "embedding_filter_threshold",
        "eval_item_timeout_seconds",
    } <= keys
    assert not (keys & {"llm_api_key", "giga_client_id", "giga_client_secret", "auth_token"})


def test_pwin_service_schema_has_coefficients() -> None:
    from zakupki_parser.api.app.config_schema import build_schema

    schema = build_schema(PwinServiceConfig)
    keys = {f["key"] for f in schema}
    assert {"base_pwin", "max_pwin_cap", "use_stub", "stub_pwin"} <= keys


def test_strip_secrets_removes_only_secret_keys() -> None:
    cfg = SERVICE_CONFIGS["scoring"]
    data = {
        "llm_base_url": "http://x",
        "llm_api_key": "sk-secret",
        "auth_token": "tok",
        "giga_client_secret": "sec",
        "score_round_digits": 2,
    }
    cleaned = _strip_secrets(data, cfg.secrets)
    assert cleaned == {"llm_base_url": "http://x", "score_round_digits": 2}


def test_validate_env_content_accepts_valid() -> None:
    _validate_env_content("# comment\nA=1\nB=тest\n\nEMPTY=\n")


def test_validate_env_content_rejects_bad_lines() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_env_content("PATH=ok\nnot-a-line\n")
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        _validate_env_content("1ABC=value\n")
    assert exc.value.status_code == 422


def test_service_paths_resolves_to_src_dir() -> None:
    state = cast(Any, types.SimpleNamespace(configs_dir="/repo/configs"))
    config_path, env_path = _service_paths(state, SERVICE_CONFIGS["scoring"])
    assert str(config_path) == "/repo/src/scoring_service/config.yaml"
    assert str(env_path) == "/repo/src/scoring_service/.env"


def test_instance_is_pydantic_model() -> None:
    # Модели наследуют BaseModel (необходимо для build_schema).
    assert issubclass(ScoringServiceConfig, BaseModel)


def test_service_configs_have_restart_launch_metadata() -> None:
    """Каждый сервис несёт метаданные для рестарта (вариант A: subprocess)."""
    for svc in SERVICE_CONFIGS.values():
        assert svc.module, f"service {svc.name}: пустой module"
        assert svc.worker_cmd, f"service {svc.name}: пустой worker_cmd"
        assert svc.parser_env, f"service {svc.name}: пустой parser_env"
        assert svc.log_name, f"service {svc.name}: пустой log_name"


def test_service_status_reports_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "zakupki_parser.api.app.routes.config.find_worker_pids", lambda module, cmd: [111, 222]
    )
    result = _service_status(SERVICE_CONFIGS["scoring"])
    assert result == {"service": "scoring", "running": True, "pids": [111, 222]}


def test_service_status_reports_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "zakupki_parser.api.app.routes.config.find_worker_pids", lambda module, cmd: []
    )
    result = _service_status(SERVICE_CONFIGS["scoring"])
    assert result == {"service": "scoring", "running": False, "pids": []}


def test_stop_service_terminates_found_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "zakupki_parser.api.app.routes.config.find_worker_pids", lambda module, cmd: [111, 222]
    )
    terminated_calls: list[list[int]] = []

    def fake_terminate(pids: list[int]) -> int:
        terminated_calls.append(pids)
        return len(pids)

    monkeypatch.setattr("zakupki_parser.api.app.routes.config.terminate_pids", fake_terminate)
    result = _stop_service(SERVICE_CONFIGS["scoring"])
    assert result == {"status": "stopped", "service": "scoring", "terminated": 2}
    assert terminated_calls == [[111, 222]]


def test_start_service_launches_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "zakupki_parser.api.app.routes.config.find_worker_pids", lambda module, cmd: []
    )
    monkeypatch.setattr("zakupki_parser.api.app.routes.config.launch_worker", lambda **kwargs: 4242)
    state = cast(Any, types.SimpleNamespace(configs_dir="/repo/configs", parser_port=8000))
    result = _start_service(state, SERVICE_CONFIGS["scoring"])
    assert result == {"status": "started", "service": "scoring", "pid": 4242}


def test_start_service_does_not_duplicate_running_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Уже запущенный сервис не должен получить дубликат-процесс."""
    monkeypatch.setattr(
        "zakupki_parser.api.app.routes.config.find_worker_pids", lambda module, cmd: [999]
    )

    def fail_if_called(**kwargs: Any) -> int:
        raise AssertionError("launch_worker не должен вызываться для уже запущенного сервиса")

    monkeypatch.setattr("zakupki_parser.api.app.routes.config.launch_worker", fail_if_called)
    state = cast(Any, types.SimpleNamespace(configs_dir="/repo/configs", parser_port=8000))
    result = _start_service(state, SERVICE_CONFIGS["scoring"])
    assert result == {"status": "already_running", "service": "scoring", "pids": [999]}
