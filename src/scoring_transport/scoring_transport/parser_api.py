"""Клиент REST API парсера закупок (только через REST, без БД парсера)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ParserApiClient:
    """Обёртка над REST API парсера."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def get_procurement(self, procurement_id: int) -> dict[str, Any]:
        """Полная карточка закупки (включая detail_json)."""
        url = f"{self._base}/api/procurements/{procurement_id}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    async def post_score(
        self,
        procurement_id: int,
        score: float,
        score_method: str = "external",
        fit_score: float | None = None,
        embedding_similarity: float | None = None,
        retry_max: int = 3,
        retry_backoff: float = 2.0,
    ) -> dict[str, Any]:
        """Вернуть результат скоринга в парсер (с ретраями/backoff)."""
        url = f"{self._base}/api/procurements/{procurement_id}/score"
        payload = {"score": score, "score_method": score_method}
        if fit_score is not None:
            payload["fit_score"] = fit_score
        if embedding_similarity is not None:
            payload["embedding_similarity"] = embedding_similarity
        last_exc: Exception | None = None
        for attempt in range(retry_max):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data: dict[str, Any] = resp.json()
                    return data
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "POST /score для %s не удался (попытка %d/%d): %s",
                    procurement_id,
                    attempt + 1,
                    retry_max,
                    exc,
                )
                await asyncio.sleep(retry_backoff * (attempt + 1))
        assert last_exc is not None
        raise last_exc
