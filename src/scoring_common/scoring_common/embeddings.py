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

    @property
    def cost_usd(self) -> float:
        """Накопленная стоимость эмбеддингов (USD)."""
        ...

    def reset_cost(self) -> None:
        """Обнулить накопленную стоимость (перед обработкой новой закупки)."""
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
        self._cost_usd: float = 0.0
        self._usage: dict[str, int] = {}
        self._cost_details: dict[str, float] = {}
        self._calls = 0
        self._latency_ms = 0.0

    @property
    def cost_usd(self) -> float:
        return round(self._cost_usd, 8)

    def reset_cost(self) -> None:
        self._cost_usd = 0.0
        self._usage = {}
        self._cost_details = {}
        self._calls = 0
        self._latency_ms = 0.0

    @property
    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    def metrics(self) -> dict[str, Any]:
        """Сырые агрегаты эмбеддингов: стоимость/токены/латенси/число вызовов."""
        return {
            "usd": round(self._cost_usd, 8),
            "usage": dict(self._usage),
            "cost_details": dict(self._cost_details),
            "models": [self._model],
            "calls": self._calls,
            "latency_ms": round(self._latency_ms, 3),
        }

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
            import time as _time

            start = _time.perf_counter()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            self._latency_ms += (_time.perf_counter() - start) * 1000.0
            items = data.get("data") or []
            # data может быть списком векторов {index, embedding}.
            ordered = sorted(items, key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in ordered]
            if len(vectors) != len(texts):
                obs.update(level="WARNING", status_message="число векторов != числу текстов")
                obs.end()
                return None
            input_tokens = embedding_input_tokens(data, texts)
            embed_usd = embedding_cost_usd(input_tokens)
            self._cost_usd += embed_usd
            self._calls += 1
            self._usage["input"] = int(self._usage.get("input") or 0) + input_tokens
            self._cost_details["input"] = round(
                (self._cost_details.get("input") or 0.0) + embed_usd, 8
            )
            obs.update(
                output={"vectors": vectors, "model": self._model},
                usage_details={"input": input_tokens},
                cost_details={"input": embed_usd},
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

    @property
    def cost_usd(self) -> float:
        return round(self._embedder.total_cost, 8)

    def reset_cost(self) -> None:
        self._embedder.reset_cost()

    def reset_metrics(self) -> None:
        self._embedder.reset_metrics()

    def metrics(self) -> dict[str, Any]:
        return self._embedder.metrics()

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
