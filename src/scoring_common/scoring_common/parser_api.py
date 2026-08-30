"""Клиент REST API парсера закупок (данные — только через REST, без БД парсера)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Активный профиль меняется редко: кешируем ответ /api/clients/active на TTL,
# чтобы воркеры каскада не дёргали парсер на каждую закупку.
_ACTIVE_CLIENT_TTL_SECONDS = 60.0
_active_client_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# Аналитические скор-настройки (config_service.yaml -> scoring) — глобальны
# (не tenant), кешируются на TTL.
_SCORING_CONFIG_TTL_SECONDS = 60.0
_scoring_config_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class ParserApiClient:
    """Обёртка над REST API парсера."""

    def __init__(
        self, base_url: str, timeout: float = 30.0, internal_token: str | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        # Внутренний токен конвейера (заголовок X-Internal-Token): используется
        # по умолчанию для всех вызовов, если не передан явно на конкретный метод.
        self._internal_token = internal_token

    def _resolve_token(self, internal_token: str | None) -> str | None:
        return internal_token if internal_token is not None else self._internal_token

    def _headers(self, internal_token: str | None) -> dict[str, str] | None:
        token = self._resolve_token(internal_token)
        return {"X-Internal-Token": token} if token else None

    async def get_procurement(self, procurement_id: int) -> dict[str, Any]:
        """Полная карточка закупки (включая detail_json).

        Карточка читается служебными сервисами конвейера, поэтому запрос идёт с
        внутренним токеном парсера (X-Internal-Token), а не пользовательским JWT.
        """
        url = f"{self._base}/api/procurements/{procurement_id}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers(None))
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    async def get_active_client(
        self, internal_token: str | None = None, profile_id: int | None = None
    ) -> dict[str, Any]:
        """Профиль клиента для анализа (компетенции, слова, вопросы к ТЗ).

        ``internal_token`` — внутренний токен парсера (заголовок X-Internal-Token).
        ``profile_id`` — конкретный профиль: передаётся заголовком ``X-Profile-ID``
        (пер-профильный скоринг, BR-07). Без него эндпоинт отдаёт 400 — сервис-аккаунта
        нет. Ответ кешируется на ``_ACTIVE_CLIENT_TTL_SECONDS`` (профиль меняется редко).
        """
        cache_key = f"{self._resolve_token(internal_token) or ''}:{profile_id or ''}"
        now = time.monotonic()
        cached = _active_client_cache.get(cache_key)
        if cached is not None and now - cached[0] < _ACTIVE_CLIENT_TTL_SECONDS:
            return cached[1]
        url = f"{self._base}/api/clients/active"
        headers = self._headers(internal_token)
        if profile_id is not None:
            headers = {**(headers or {}), "X-Profile-ID": str(profile_id)}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        _active_client_cache[cache_key] = (time.monotonic(), data)
        return data

    async def get_scoring_config(self, internal_token: str | None = None) -> dict[str, Any]:
        """Аналитические скор-настройки (config_service.yaml -> scoring) из парсера.

        ``internal_token`` — внутренний токен парсера (X-Internal-Token): эндпоинт
        /api/config/scoring открыт и для конвейера, и для пользователей. Ответ
        кешируется на ``_SCORING_CONFIG_TTL_SECONDS`` (настройки меняются редко).
        """
        now = time.monotonic()
        cached = _scoring_config_cache.get("global")
        if cached is not None and now - cached[0] < _SCORING_CONFIG_TTL_SECONDS:
            return cached[1]
        url = f"{self._base}/api/config/scoring"
        headers = self._headers(internal_token)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        _scoring_config_cache["global"] = (time.monotonic(), data)
        return data

    async def post_score(
        self,
        procurement_id: int,
        score: float,
        score_method: str = "fit",
        fit_score: float | None = None,
        embedding_similarity: float | None = None,
        langfuse_trace_url: str | None = None,
        p_win: float | None = None,
        margin: float | None = None,
        rag_report: dict[str, Any] | None = None,
        score_costs: dict[str, Any] | None = None,
        profile_id: int | None = None,
        retry_max: int = 3,
        retry_backoff: float = 2.0,
        internal_token: str | None = None,
    ) -> dict[str, Any]:
        """Вернуть результат скоринга в парсер (с ретраями/backoff).

        ``internal_token`` — внутренний токен парсера (заголовок X-Internal-Token):
        служебные эндпоинты парсера (POST /score) доступны только конвейеру.
        ``rag_report`` — результат RAG-анализа стоп-условий (analysis_service).
        ``score_costs`` — стоимость обработки (скоринг/анализ) для поля ``costs``.
        ``profile_id`` — профиль, для которого посчитан результат (пер-профильно, BR-07).
        """
        url = f"{self._base}/api/procurements/{procurement_id}/score"
        payload = {"score": score, "score_method": score_method}
        if profile_id is not None:
            payload["profile_id"] = profile_id
        if fit_score is not None:
            payload["fit_score"] = fit_score
        if embedding_similarity is not None:
            payload["embedding_similarity"] = embedding_similarity
        if langfuse_trace_url is not None:
            payload["langfuse_trace_url"] = langfuse_trace_url
        if p_win is not None:
            payload["p_win"] = p_win
        if margin is not None:
            payload["margin"] = margin
        if rag_report is not None:
            payload["rag_report"] = rag_report
        if score_costs is not None:
            payload["score_costs"] = score_costs
        headers = self._headers(internal_token)
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
