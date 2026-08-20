"""FastAPI-приложение транспорта скоринга.

- ``POST /api/scoring/jobs`` — ingest: принять запрос на скоринг закупки,
  получить карточку из парсера, поставить в приоритетную Redis-очередь.
- ``GET /health`` — статус.
- Фоновый consumer возвращает результаты в парсер (см. consumers/results.py).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from scoring_common.parser_api import ParserApiClient
from scoring_transport.broker.redis_queue import TransportQueue
from scoring_transport.consumers.results import ResultsConsumer
from scoring_transport.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def _auth_dependency(settings: Settings) -> Callable[[str | None], None]:
    """FastAPI-зависимость опциональной авторизации по Bearer-токену."""

    def _require_authorization(
        authorization: str | None = Header(default=None),
    ) -> None:
        if not settings.auth_token:
            return
        if authorization != f"Bearer {settings.auth_token}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    return _require_authorization


class ScoringJobRequest(BaseModel):
    """Запрос на скоринг закупки."""

    procurement_id: int
    priority: float | None = Field(
        default=None,
        description="априорный приоритет (дефолтный score); если пуст — возьмётся из карточки",
    )
    stage: str = Field(
        default="fit",
        description="стадия: fit | pwin | margin | analysis",
    )


class ScoringJobOut(BaseModel):
    procurement_id: int
    priority: float
    status: str = "enqueued"


class HealthOut(BaseModel):
    status: str
    parser_api: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    queue = TransportQueue(settings)
    parser = ParserApiClient(settings.parser_api_url)
    consumer = ResultsConsumer(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await queue.connect()

        def _on_consumer_done(task: asyncio.Task[None]) -> None:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.exception("Consumer task завершился ошибкой: %s", exc)

        task = asyncio.create_task(consumer.run_forever())
        task.add_done_callback(_on_consumer_done)
        yield
        task.cancel()
        await queue.close()

    app = FastAPI(title="Zakupki Scoring Transport", version="0.1.0", lifespan=lifespan)
    auth = _auth_dependency(settings)

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok", parser_api=settings.parser_api_url)

    @app.post(
        "/api/scoring/jobs",
        response_model=ScoringJobOut,
        status_code=202,
        dependencies=[Depends(auth)],
    )
    async def create_job(body: ScoringJobRequest) -> ScoringJobOut:
        try:
            # Проверяем, что закупка существует в парсере (приоритет передаётся
            # из парсера в авто-пуше, ADR-7; здесь — fallback на priority_default).
            await parser.get_procurement(body.procurement_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось получить карточку %s: %s", body.procurement_id, exc)
            raise HTTPException(
                status_code=502, detail="Парсер недоступен или закупка не найдена"
            ) from exc

        priority = body.priority if body.priority is not None else settings.priority_default
        await queue.enqueue(body.procurement_id, priority, stage=body.stage)
        logger.info(
            "Задача на скоринг закупки %s поставлена (stage=%s, priority=%.2f)",
            body.procurement_id,
            body.stage,
            priority,
        )
        return ScoringJobOut(
            procurement_id=body.procurement_id, priority=priority, status="enqueued"
        )

    return app
