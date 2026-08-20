"""Клиент REST API парсера закупок (данные — только через REST, без БД парсера)."""

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

    async def get_active_client(self, internal_token: str | None = None) -> dict[str, Any]:
        """Активный клиентский профиль (компетенции, слова, вопросы к ТЗ).

        ``internal_token`` — внутренний токен парсера (заголовок X-Internal-Token):
        эндпоинт /api/clients/active открыт и для конвейера, и для пользователей.
        """
        url = f"{self._base}/api/clients/active"
        headers = {"X-Internal-Token": internal_token} if internal_token else None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    async def post_score(
        self,
        procurement_id: int,
        score: float,
        score_method: str = "fit",
        fit_score: float | None = None,
        embedding_similarity: float | None = None,
        p_win: float | None = None,
        margin: float | None = None,
        rag_report: dict[str, Any] | None = None,
        retry_max: int = 3,
        retry_backoff: float = 2.0,
        internal_token: str | None = None,
    ) -> dict[str, Any]:
        """Вернуть результат скоринга в парсер (с ретраями/backoff).

        ``internal_token`` — внутренний токен парсера (заголовок X-Internal-Token):
        служебные эндпоинты парсера (POST /score) доступны только конвейеру.
        ``rag_report`` — результат RAG-анализа стоп-условий (analysis_service).
        """
        url = f"{self._base}/api/procurements/{procurement_id}/score"
        payload = {"score": score, "score_method": score_method}
        if fit_score is not None:
            payload["fit_score"] = fit_score
        if embedding_similarity is not None:
            payload["embedding_similarity"] = embedding_similarity
        if p_win is not None:
            payload["p_win"] = p_win
        if margin is not None:
            payload["margin"] = margin
        if rag_report is not None:
            payload["rag_report"] = rag_report
        headers = {"X-Internal-Token": internal_token} if internal_token else None
        last_exc: Exception | None = None
        for attempt in range(retry_max):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
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
