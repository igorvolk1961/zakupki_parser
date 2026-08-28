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
    assert {"base_pwin", "k_ai", "max_pwin_cap", "use_stub", "ai_markers"} <= keys


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
