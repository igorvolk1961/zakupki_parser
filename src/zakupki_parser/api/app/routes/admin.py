"""Административные эндпоинты: демо, health, управление парсером и БД."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlalchemy import text as sql_text

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import ClearIrrelevantIn, HealthOut
from zakupki_parser.api.app.state import _broadcast, _run_parser
from zakupki_parser.auth import decode_token
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)

# Web-демо: страница лежит рядом с прежним api/app.py (каталог api/).
ZAKUPKI_HTML = Path(__file__).resolve().parents[2] / "zakupki.html"


def build_admin_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    _active_context = ctx._active_context
    require_user = ctx.require_user
    require_admin = ctx.require_admin

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def demo() -> HTMLResponse:
        """Простое web-приложение для демонстрации MVP (читает данные через API)."""
        # Без кеширования: браузер всегда получает свежую версию HTML (ранее
        # кешированная промежуточная версия показывала устаревший интерфейс).
        return HTMLResponse(
            ZAKUPKI_HTML.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        db_ok = False
        if state.db is not None:
            try:
                async with state.db.session() as session:
                    await session.execute(sql_text("SELECT 1"))
                db_ok = True
            except Exception:  # noqa: BLE001
                db_ok = False
        return HealthOut(status="ok", db=db_ok)

    @router.websocket("/ws")
    async def ws_updates(websocket: WebSocket) -> None:
        """Канал живых обновлений: шлёт 'data-changed' при изменении БД.

        При включённой авторизации токен передаётся query-параметром ``?token=``
        (браузер не может задать заголовок WebSocket-запроса).
        """
        if state.cfg.ops.auth.enabled:
            token = websocket.query_params.get("token")
            payload = decode_token(token or "", state.cfg.ops.auth.secret or "")
            if payload is None or await _repo().get_user(payload["sub"]) is None:
                await websocket.close(code=1008)
                return
        await websocket.accept()
        state.ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.ws_clients.discard(websocket)

    @router.get("/api/parser/status", include_in_schema=False, dependencies=[Depends(require_user)])
    async def parser_status() -> dict[str, Any]:
        """Текущее состояние парсера (запущен/остановлен, ошибка, время)."""
        status = dict(state.parser_status)
        if state.parser_task is not None and not state.parser_task.done():
            status["running"] = True
        return status

    @router.post(
        "/api/parser/start",
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    async def start_parser() -> dict[str, Any]:
        """Запускает постоянный мониторинг парсера (периодические проходы) в фоне."""
        async with state.parser_lock:
            if state.parser_task is not None and not state.parser_task.done():
                raise HTTPException(status_code=409, detail="Парсер уже запущен")
            state.parser_status = {
                "running": True,
                "stopped": False,
                "error": None,
                "started_at": datetime.now(UTC).isoformat(),
                "finished_at": None,
            }
            state.parser_task = asyncio.create_task(_run_parser(state))
        logger.info("Запущен парсер (постоянный мониторинг) по команде из web-демо")
        return {"status": "started"}

    @router.post("/api/parser/stop", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def stop_parser() -> dict[str, Any]:
        """Останавливает запущенный проход парсера."""
        task = state.parser_task
        if task is None or task.done():
            return {"status": "idle"}
        task.cancel()
        logger.info("Запрошена остановка парсера из web-демо")
        return {"status": "stopping"}

    @router.post("/api/db/clear", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def clear_db() -> dict[str, Any]:
        """Очищает БД (закупки и заказчики). Доступно только при остановленном парсере."""
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        deleted = await _repo().clear_all()
        logger.info("БД очищена из web-демо: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    @router.post(
        "/api/db/clear-inactive",
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    async def clear_inactive() -> dict[str, Any]:
        """Удаляет неактивные закупки (is_active=false или истёкший срок актуальности).

        Клиентская операция: активность учитывает текущую дату, как в фильтре
        ``active``. Доступно только при остановленном парсере.
        """
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        deleted = await _repo().delete_inactive()
        logger.info("Удалены неактивные закупки из web-демо: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    @router.post(
        "/api/db/clear-irrelevant",
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    async def clear_irrelevant(
        body: ClearIrrelevantIn | None = None, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Удаляет нерелевантные закупки среди обработанных сервисом скоринга.

        Учитываются только записи с score_method=external и fit_score < порога.
        Записи без внешнего скоринга не затрагиваются. Доступно только при
        остановленном парсере.
        """
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        threshold = body.min_fit_score if body is not None else 0.4
        _, profile = await _active_context(user)
        deleted = await _repo().delete_irrelevant(threshold, profile_id=profile.id)
        logger.info("Удалены нерелевантные закупки из web-демо: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    return router
