"""Unit-тесты фабрики эмбеддингов (analysis_service.embedder) и Giga-обёртки."""

from __future__ import annotations

import asyncio

import pytest
from analysis_service.embedder import build_embedder
from analysis_service.settings import Settings

from scoring_common.embeddings import EmbeddingClient, GigaEmbeddingClient


def test_build_embedder_prefers_giga_when_configured() -> None:
    settings = Settings(giga_client_id="cid", giga_client_secret="secret")
    assert settings.giga_configured is True
    embedder = build_embedder(settings)
    assert isinstance(embedder, GigaEmbeddingClient)


def test_build_embedder_falls_back_to_proxy() -> None:
    settings = Settings(giga_client_id="", giga_client_secret="")
    assert settings.giga_configured is False
    embedder = build_embedder(settings)
    assert isinstance(embedder, EmbeddingClient)


def _client() -> GigaEmbeddingClient:
    return GigaEmbeddingClient(
        base_url="http://x",
        model="EmbeddingsGigaR",
        auth_url="http://x/oauth",
        client_id="cid",
        client_secret="secret",
    )


def test_giga_embedding_client_returns_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(client._embedder, "embed", lambda texts: [[1.0, 2.0], [3.0, 4.0]])
    assert asyncio.run(client.embed(["a", "b"])) == [[1.0, 2.0], [3.0, 4.0]]
    assert asyncio.run(client.embed_one("a")) == [1.0, 2.0]
    assert asyncio.run(client.embed([])) == []


def test_giga_embedding_client_degrades_to_none_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()

    def boom(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("All connection attempts failed")

    monkeypatch.setattr(client._embedder, "embed", boom)
    assert asyncio.run(client.embed(["text"])) is None
    assert asyncio.run(client.embed_one("text")) is None
