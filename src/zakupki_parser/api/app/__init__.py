"""FastAPI-сервис: чтение закупок из БД, web-демо и управление парсером.

``create_app`` собирает приложение из роутеров (подпакет ``routes``); состояние,
схемы, зависимости и конвертеры вынесены в соседние модули пакета
(``state``, ``schemas``, ``deps``, ``converters``). Статические ассеты
web-интерфейса (CSS/JS-модули) раздаются из каталога ``api/static``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from zakupki_parser.api.app.converters import _meets_stage_notify_threshold  # noqa: F401
from zakupki_parser.api.app.deps import build_context
from zakupki_parser.api.app.routes.admin import build_admin_router
from zakupki_parser.api.app.routes.auth import build_auth_router
from zakupki_parser.api.app.routes.clients import build_clients_router
from zakupki_parser.api.app.routes.config import build_config_router
from zakupki_parser.api.app.routes.customers import build_customers_router
from zakupki_parser.api.app.routes.facts import build_facts_router
from zakupki_parser.api.app.routes.procurements import build_procurements_router
from zakupki_parser.api.app.state import _create_state
from zakupki_parser.notify import Notifier
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)

# Каталог статических ассетов web-интерфейса (api/static).
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

__all__ = ["create_app", "_meets_stage_notify_threshold"]


def create_app(configs_dir: str = "configs") -> FastAPI:
    state = _create_state(configs_dir)
    ctx = build_context(state)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(state.cfg.ops.db)
        try:
            await db.connect()
            state.db = db
            state.repository = ProcurementRepository(db)
            # Сид справочников профиля (типы лицензий, типы подтверждения BR-03) —
            # идемпотентно, чтобы они были и при неполной миграции.
            await state.repository.ensure_reference_data()
        except Exception as exc:  # noqa: BLE001
            logger.error("БД недоступна при старте API: %s", exc)
            state.db = None
            state.repository = None
        else:
            # Сид начального администратора — отдельный try: его сбой не должен
            # «ломать» общее состояние БД (иначе весь API уйдёт в 503).
            if state.cfg.ops.auth.enabled:
                try:
                    await ctx._seed_initial_admin()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Не удалось создать начального администратора: %s", exc)
        yield
        if state.db is not None:
            await state.db.dispose()

    app = FastAPI(title="Zakupki Parser API", version="0.1.0", lifespan=lifespan)
    app.state.parser = state

    # Уведомления подписчиков — отправляются в POST /score после прихода внешнего
    # скора и прохождения порога notify_min_fit_score (ADR-7).
    state.notifier = Notifier(state.cfg.ops.notifications)
    state.notify_min_fit_score = state.cfg.ops.notifications.notify_min_fit_score

    # Статические ассеты web-интерфейса (CSS + ES-модули JS).
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    app.include_router(build_admin_router(ctx))
    app.include_router(build_auth_router(ctx))
    app.include_router(build_procurements_router(ctx))
    app.include_router(build_clients_router(ctx))
    app.include_router(build_facts_router(ctx))
    app.include_router(build_customers_router(ctx))
    app.include_router(build_config_router(ctx))
    return app
