"""Клиент REST API парсера закупок (данные — только через REST, без БД парсера)."""

from __future__ import annotations

from typing import Any

import httpx


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
