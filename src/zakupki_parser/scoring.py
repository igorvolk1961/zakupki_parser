"""Клиент transport-конвейера внешнего скоринга (ADR-7).

Дефолтного (внутреннего) скоринга больше НЕТ: закупка сохраняется без оценки,
внешний каскад (Fit/P(win)/Margin) считает результаты и пишет их в
``procurement_evaluations`` (per-profile) через ``POST /score``. Приоритет очереди —
время обновления/публикации закупки (см. orchestrator и scheduler recovery).
"""

from __future__ import annotations

import httpx


class ScoringTransportClient:
    """Клиент transport-конвейера скоринга (авто-пуш задания после сохранения, ADR-7).

    Вызов best-effort: при недоступности транспорта задание не ставится, но «сырая»
    закупка уже сохранена в БД (вежливая деградация; recovery догонит её по
    ``scoring_queued_at``).
    """

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self._base = url.rstrip("/")
        self._timeout = timeout
        # Постоянный клиент для продакшена (не создаётся на каждый вызов);
        # при передаче ``transport`` (тесты) используется одноразовый клиент.
        self._client: httpx.AsyncClient | None = None

    async def enqueue(
        self,
        procurement_id: int,
        priority: float,
        transport: httpx.AsyncBaseTransport | None = None,
        stage: str = "fit",
    ) -> None:
        """Поставить задание на скоринг: POST /api/scoring/jobs.

        ``stage`` — стадия (fit/pwin/margin/analysis); транспорт направляет задание
        в соответствующую Redis-очередь.
        """
        url = f"{self._base}/api/scoring/jobs"
        payload = {"procurement_id": procurement_id, "priority": priority, "stage": stage}
        if transport is not None:
            async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
