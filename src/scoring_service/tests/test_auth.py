"""Тесты обязательной авторизации эндпоинта /score."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scoring_service.settings import Settings
from scoring_service.web.app import create_app


def _client(token: str) -> TestClient:
    return TestClient(create_app(Settings(auth_token=token)))


def test_score_requires_token() -> None:
    client = _client("secret")
    resp = client.post("/score", json={"record": {"subject": "x"}})
    assert resp.status_code == 401
    wrong = client.post(
        "/score", json={"record": {"subject": "x"}}, headers={"Authorization": "Bearer wrong"}
    )
    assert wrong.status_code == 401


def test_score_accepted_with_token() -> None:
    client = _client("secret")
    # 401 показывает, что авторизация обязательна; до LLM корректный запрос не доходит,
    # но с валидным токеном ответ уже не является 401.
    resp = client.post(
        "/score",
        json={"record": {"subject": "x"}},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code != 401


def test_health_open_when_token_set() -> None:
    client = _client("secret")
    assert client.get("/health").status_code == 200


def test_app_requires_token() -> None:
    # Без токена веб-сервис не собирается: авторизацию нельзя отключить.
    with pytest.raises(RuntimeError):
        create_app(Settings())
