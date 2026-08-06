"""Тесты опциональной авторизации эндпоинта /score."""

from __future__ import annotations

from fastapi.testclient import TestClient
from scoring_service.settings import Settings
from scoring_service.web.app import create_app


def _client(token: str | None) -> TestClient:
    return TestClient(create_app(Settings(auth_token=token)))


def test_score_requires_token_when_set() -> None:
    client = _client("secret")
    resp = client.post("/score", json={"record": {"subject": "x"}})
    assert resp.status_code == 401
    wrong = client.post(
        "/score", json={"record": {"subject": "x"}}, headers={"Authorization": "Bearer wrong"}
    )
    assert wrong.status_code == 401


def test_health_open_when_token_set() -> None:
    client = _client("secret")
    assert client.get("/health").status_code == 200


def test_score_open_when_token_unset() -> None:
    client = _client(None)
    # без токена auth-зависимость пропускает; до LLM не доходит корректного запроса,
    # но 401 не возвращается — значит доступ открыт
    assert client.post("/score", json={"record": {"subject": "x"}}).status_code != 401
