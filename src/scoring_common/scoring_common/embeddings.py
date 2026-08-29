"""Клиенты эмбеддингов для RAG-анализа (analysis_service).

Два бэкенда:
- ``EmbeddingClient`` — любой OpenAI-совместимый endpoint ``/embeddings``
  (например, Giga через gpt2giga-прокси).
- ``GigaEmbeddingClient`` — прямой Giga Embedder (Sber GigaChat) с автообновлением
  OAuth-токена (см. ``scoring_common.giga``); асинхронная обёртка над синхронным
  ``GigaEmbedder``. Использует те же модель и ключи, что и scoring_service.

Оба реализуют общий интерфейс ``Embeddable``: ``await embed(...)`` /
``await embed_one(...)`` и возвращают ``None`` при сбое (best-effort, не роняют задание).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import httpx

from scoring_common.costing import embedding_cost_usd, embedding_input_tokens
from scoring_common.giga import GigaEmbedder, GigaTokenProvider
from scoring_common.langfuse import start_observation

logger = logging.getLogger(__name__)


class Embeddable(Protocol):
    """Асинхронный клиент эмбеддингов, ожидаемый RAG-пайплайном."""

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Векторы для текстов (пакетно); None — сбой."""
        ...

    async def embed_one(self, text: str) -> list[float] | None:
        """Вектор одного текста; None — сбой."""
        ...


class EmbeddingClient:
    """Вычисление эмбеддингов через OpenAI-совместимый endpoint ``/embeddings``."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Векторы для текстов (пакетно). None — сбой (best-effort, не роняет задание)."""
        if not texts:
            return []
        url = f"{self._base}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        obs = start_observation(
            name="embeddings",
            as_type="embedding",
            input=payload,
            metadata={"model": self._model},
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            items = data.get("data") or []
            # data может быть списком векторов {index, embedding}.
            ordered = sorted(items, key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in ordered]
            if len(vectors) != len(texts):
                obs.update(level="WARNING", status_message="число векторов != числу текстов")
                obs.end()
                return None
            input_tokens = embedding_input_tokens(data, texts)
            obs.update(
                output={"vectors": vectors, "model": self._model},
                usage_details={"input": input_tokens},
                cost_details={"input": embedding_cost_usd(input_tokens)},
            )
            obs.end()
            return vectors
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            obs.update(level="WARNING", status_message=f"сбой эмбеддингов: {exc}")
            obs.end()
            logger.warning("Не удалось вычислить эмбеддинги (%s): %s", self._model, exc)
            return None

    async def embed_one(self, text: str) -> list[float] | None:
        vectors = await self.embed([text])
        if not vectors:
            return None
        return vectors[0]


class GigaEmbeddingClient:
    """Прямые эмбеддинги Giga (OAuth-токен, модель EmbeddingsGigaR) — async.

    Асинхронная обёртка над синхронным ``scoring_common.giga.GigaEmbedder``: вызовы
    выполняются в пуле потоков. Контракт совпадает с ``EmbeddingClient``
    (``embed``/``embed_one``, ``None`` при сбое).
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        auth_url: str,
        client_id: str,
        client_secret: str,
        scope: str = "GIGACHAT_API_PERS",
        timeout: float = 30.0,
        min_token_ttl_seconds: float = 60.0,
        verify_ssl: bool = True,
    ) -> None:
        self._model = model
        token_provider = GigaTokenProvider(
            auth_url=auth_url,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            min_ttl_seconds=min_token_ttl_seconds,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        self._embedder = GigaEmbedder(
            base_url=base_url,
            model=model,
            token_provider=token_provider,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Векторы для текстов (пакетно). None — сбой (best-effort, не роняет задание)."""
        if not texts:
            return []
        try:
            return await asyncio.to_thread(self._embedder.embed, texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось вычислить эмбеддинги (%s): %s", self._model, exc)
            return None

    async def embed_one(self, text: str) -> list[float] | None:
        vectors = await self.embed([text])
        if not vectors:
            return None
        return vectors[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Косинусная близость двух векторов (0..1); при вырожденных — 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot: float = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a: float = sum(x * x for x in a) ** 0.5
    norm_b: float = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
