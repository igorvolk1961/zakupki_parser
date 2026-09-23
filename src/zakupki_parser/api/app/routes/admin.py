"""Административные эндпоинты: главная страница, health, управление парсером и БД."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlalchemy import text as sql_text

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import ClearDbIn, ClearIrrelevantIn, HealthOut
from zakupki_parser.api.app.state import _broadcast, _spawn_parser
from zakupki_parser.auth import decode_token
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)

# Web-интерфейс: страница лежит рядом с прежним api/app.py (каталог api/).
ZAKUPKI_HTML = Path(__file__).resolve().parents[2] / "zakupki.html"


def build_admin_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    _active_context = ctx._active_context
    require_user = ctx.require_user
    require_devops = ctx.require_devops

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        """Простое web-приложение MVP (читает данные через API)."""
        # Без кеширования: браузер всегда получает свежую версию HTML (ранее
        # кешированная промежуточная версия показывала устаревший интерфейс).
        # token_storage (config_ops.yaml -> auth) — куда фронт кладёт bearer-токен
        # (api.js): подмешиваем инлайн-скриптом ДО модульных <script> (main.js и
        # т.п.), т.к. authToken()/setToken() читают его при первом же вызове.
        # Значение — литерал из Literal["local","session"] (не пользовательский
        # ввод), экранирование не требуется.
        html = ZAKUPKI_HTML.read_text(encoding="utf-8").replace(
            "<head>",
            f'<head>\n<script>window.__TOKEN_STORAGE__="{state.cfg.ops.auth.token_storage}";</script>',
            1,
        )
        return HTMLResponse(
            html,
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

        Авторизация всегда включена — токен передаётся query-параметром ``?token=``
        (браузер не может задать заголовок WebSocket-запроса).
        """
        token = websocket.query_params.get("token")
        payload = decode_token(token or "", state.cfg.ops.auth.secret or "")
        if payload is None:
            logger.info("WebSocket отклонён: недействительный или истёкший токен")
            await websocket.close(code=1008)
            return
        user = await _repo().get_user(payload["sub"])
        if user is None or user.status == "blocked":
            logger.info("WebSocket отклонён: пользователь недоступен или заблокирован")
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

    @router.get(
        "/api/parser/status",
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def parser_status() -> dict[str, Any]:
        """Текущее состояние парсера (запущен/остановлен, ошибка, время).

        Статус доступен всем аутентифицированным пользователям: он лишь
        информирует о работе парсера, управление (start/stop/clear) — только
        devops (см. соответствующие эндпоинты).
        """
        status = dict(state.parser_status)
        if state.parser_task is not None and not state.parser_task.done():
            status["running"] = True
        return status

    @router.post(
        "/api/parser/start",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def start_parser() -> dict[str, Any]:
        """Запускает постоянный мониторинг парсера (периодические проходы) в фоне."""
        async with state.parser_lock:
            if state.parser_task is not None and not state.parser_task.done():
                raise HTTPException(status_code=409, detail="Парсер уже запущен")
            _spawn_parser(state)
        logger.info("Запущен парсер (постоянный мониторинг) по команде из web-интерфейса")
        return {"status": "started"}

    @router.post(
        "/api/parser/stop",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def stop_parser() -> dict[str, Any]:
        """Останавливает запущенный проход парсера."""
        task = state.parser_task
        if task is None or task.done():
            return {"status": "idle"}
        task.cancel()
        logger.info("Запрошена остановка парсера из web-интерфейса")
        return {"status": "stopping"}

    @router.post(
        "/api/parser/restart",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def restart_parser() -> dict[str, Any]:
        """Перезапускает парсер: останавливает текущий проход (если запущен)
        и запускает постоянный мониторинг заново."""
        async with state.parser_lock:
            task = state.parser_task
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                # _run_parser сбрасывает state.parser_task в блоке finally.
            _spawn_parser(state)
        logger.info("Перезапущен парсер по команде из web-интерфейса")
        return {"status": "restarting"}

    @router.post(
        "/api/parser/restart-process",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def restart_parser_process() -> dict[str, Any]:
        """Полный перезапуск процесса `zp serve` (не только цикла мониторинга).

        В отличие от ``/api/parser/restart`` (перезапускает только внутреннюю
        asyncio-задачу обхода в ТОМ ЖЕ процессе — код, загруженный при старте,
        не меняется), здесь процесс полностью заменяет себя (``os.execv``,
        тот же PID) и заново импортирует весь код с диска — единственный способ
        подхватить изменения кода самого парсера со страницы конфига, без
        доступа к командной строке.

        Недоступно, пока идёт обход площадок (``state.parser_task`` активна) —
        Playwright/браузер будет убит резко, без штатного закрытия.
        """
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(
                status_code=409, detail="Остановите мониторинг площадок перед перезапуском процесса"
            )
        logger.info("Полный перезапуск процесса парсера (os.execv) по команде из web-интерфейса")

        async def _delayed_execv() -> None:
            # Пауза даёт uvicorn время отправить HTTP-ответ до замены процесса.
            await asyncio.sleep(0.3)
            os.execv(sys.executable, [sys.executable, *sys.argv])

        asyncio.create_task(_delayed_execv())
        return {"status": "restarting_process"}

    @router.post("/api/db/clear", include_in_schema=False, dependencies=[Depends(require_devops)])
    async def clear_db(body: ClearDbIn | None = None) -> dict[str, Any]:
        """Очищает БД (закупки и заказчики). Доступно только при остановленном парсере.

        Закупки, принятые «в работу» (``procurements.in_work``), по умолчанию
        сохраняются. Полное удаление, включая «в работе», — только при явном
        ``include_in_work=true`` (запрашивается в web-интерфейсе).
        """
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        include_in_work = bool(body.include_in_work) if body is not None else False
        deleted = await _repo().clear_all(include_in_work=include_in_work)
        logger.info("БД очищена из web-интерфейса: %s (в работе: %s)", deleted, include_in_work)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    @router.post(
        "/api/db/clear-inactive",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
    )
    async def clear_inactive() -> dict[str, Any]:
        """Удаляет неактивные закупки (is_active=false или истёкший срок актуальности).

        Клиентская операция: активность учитывает текущую дату, как в фильтре
        ``active``. Доступно только при остановленном парсере.
        """
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        deleted = await _repo().delete_inactive()
        logger.info("Удалены неактивные закупки из web-интерфейса: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    @router.post(
        "/api/db/clear-irrelevant",
        include_in_schema=False,
        dependencies=[Depends(require_devops)],
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
        deleted = await _repo().delete_irrelevant(
            threshold, profile_id=profile.id if profile is not None else None
        )
        logger.info("Удалены нерелевантные закупки из web-интерфейса: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    return router
