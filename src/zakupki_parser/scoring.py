"""Клиент transport-конвейера внешнего скоринга (ADR-7).

Дефолтного (внутреннего) скоринга больше НЕТ: закупка сохраняется без оценки,
внешний каскад (Fit/P(win)/Margin) считает результаты и пишет их в
``procurement_evaluations`` (per-profile) через ``POST /score``. Приоритет очереди —
время обновления/публикации закупки (см. orchestrator и scheduler recovery).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ScoringTransportClient:
    """Клиент transport-конвейера скоринга (авто-пуш задания после сохранения, ADR-7).

    Вызов best-effort: при недоступности транспорта задание не ставится, но «сырая»
    закупка уже сохранена в БД (вежливая деградация; recovery догонит её по
    ``scoring_queued_at``).
    """

    def __init__(self, url: str, auth_token: str | None = None, timeout: float = 5.0) -> None:
        self._base = url.rstrip("/")
        self._timeout = timeout
        # Bearer-токен авторизации на транспорте (внутренний service-to-service
        # секрет): при пустом значении заголовок не отправляется.
        self._auth_token = auth_token
        # Постоянный клиент для продакшена (не создаётся на каждый вызов);
        # при передаче ``transport`` (тесты) используется одноразовый клиент.
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else {}

    async def enqueue(
        self,
        procurement_id: int,
        priority: float,
        transport: httpx.AsyncBaseTransport | None = None,
        stage: str = "fit",
        profile_id: int | None = None,
    ) -> None:
        """Поставить задание на скоринг: POST /api/scoring/jobs.

        ``stage`` — стадия (fit/pwin/margin/analysis); транспорт направляет задание
        в соответствующую Redis-очередь. ``profile_id`` — профиль, по компетенциям
        которого считается скор (пер-профильно, BR-07); обязателен для fit-стадии.
        """
        url = f"{self._base}/api/scoring/jobs"
        payload = {
            "procurement_id": procurement_id,
            "priority": priority,
            "stage": stage,
        }
        if profile_id is not None:
            payload["profile_id"] = profile_id
        headers = self._headers()
        if transport is not None:
            async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()

    async def queue_status(
        self, transport: httpx.AsyncBaseTransport | None = None
    ) -> dict[str, Any]:
        """Глубина очередей всех стадий каскада (devops-мониторинг, GET /api/status/queues).

        Best-effort, как и ``enqueue``: недоступность транспорта не бросает —
        вкладка мониторинга должна показать «транспорт недоступен», а не упасть.
        """
        url = f"{self._base}/api/status/queues"
        headers = self._headers()
        try:
            if transport is not None:
                async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
            else:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self._timeout)
                resp = await self._client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось получить статус очередей транспорта: %s", exc)
            return {"available": False}
        # ``data`` — {"queues": {"fit": {"jobs":.., "results":..}, ...}} (QueueDepthsOut).
        # Разворачиваем вложенный ключ "queues" в плоский словарь стадий: monitoring.js
        # (renderQueues) ожидает {available, fit: {...}, pwin: {...}, ...} на одном уровне,
        # а не {available, queues: {...}} — иначе на вкладке «Мониторинг» вместо реальных
        # стадий рендерится одна строка "queues" с v.jobs/v.results === undefined.
        return {"available": True, **data.get("queues", {})}
