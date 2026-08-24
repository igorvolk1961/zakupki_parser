"""Клиент эмбеддингов (OpenAI-совместимый API).

Используется RAG-анализом (analysis_service): эмбеддинги вопросов и чанков ТЗ.
Backend — Giga Embedder (EmbeddingsGigaR) через прокси gpt2giga, переводящий
OpenAI API в формат GigaChat, либо любой OpenAI-совместимый endpoint /embeddings.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from scoring_common.langfuse import start_observation

logger = logging.getLogger(__name__)


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
            obs.update(output={"vectors": vectors, "model": self._model})
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
