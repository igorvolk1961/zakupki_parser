"""FastAPI-сервис: чтение закупок из БД и извлечение файлов из хранилища."""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse as urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text as sql_text

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.object_store import ObjectStore, build_object_store
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Схемы ответов
# --------------------------------------------------------------------------- #
class ProcurementOut(BaseModel):
    """Карточка закупки (без detail_json)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    source_platform: str
    url: str | None = None
    customer: str | None = None
    law: str | None = None
    subject: str | None = None
    nmck: float | None = None
    publication_date: datetime | None = None
    update_date: datetime | None = None
    deadline: datetime | None = None
    execution_term: str | None = None
    okpd2_codes: str | None = None
    kpgz_codes: str | None = None
    security_amount: float | None = None
    security_amount_unit: str | None = None
    technical_spec_url: str | None = None
    technical_spec_name: str | None = None
    files_json: list[dict[str, Any]] | None = None
    score: float | None = None
    score_method: str | None = None
    created_at: datetime
    updated_at: datetime


class ProcurementDetailOut(ProcurementOut):
    """Карточка закупки с полным detail_json."""

    detail_json: dict[str, Any] | None = None


class ProcurementListOut(BaseModel):
    total: int
    items: list[ProcurementOut]


class HealthOut(BaseModel):
    status: str
    db: bool
    storage: str


class ScoreUpdate(BaseModel):
    """Обновление score внешним сервисом (по его инициативе)."""

    score: float
    score_method: str = "external"


class TechnicalSpecUpdate(BaseModel):
    """Метаданные ТЗ, возвращаемые внешним сервисом обработки файлов.

    Внешний сервис скачивает файлы закупки (в т.ч. извлекает ТЗ из ZIP-архивов)
    и возвращает имя/URL файла технического задания для записи в БД.
    """

    name: str | None = None
    url: str | None = None


# --------------------------------------------------------------------------- #
# Приложение
# --------------------------------------------------------------------------- #
class AppState:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.db: Database | None = None
        self.repository: ProcurementRepository | None = None
        self.store: ObjectStore = build_object_store(
            cfg.service.storage, Path(cfg.service.documents_dir).resolve()
        )


def _create_state(configs_dir: str) -> AppState:
    cfg = load_config(configs_dir)
    return AppState(cfg)


def create_app(configs_dir: str = "configs") -> FastAPI:
    state = _create_state(configs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(state.cfg.service.db)
        try:
            await db.connect()
            state.db = db
            state.repository = ProcurementRepository(db)
        except Exception as exc:  # noqa: BLE001
            logger.error("БД недоступна при старте API: %s", exc)
            state.db = None
            state.repository = None
        yield
        if state.db is not None:
            await state.db.dispose()

    app = FastAPI(title="Zakupki Parser API", version="0.1.0", lifespan=lifespan)
    app.state.parser = state

    def _repo() -> ProcurementRepository:
        if state.repository is None:
            raise HTTPException(status_code=503, detail="БД недоступна")
        return state.repository

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        db_ok = False
        if state.db is not None:
            try:
                async with state.db.session() as session:
                    await session.execute(sql_text("SELECT 1"))
                db_ok = True
            except Exception:  # noqa: BLE001
                db_ok = False
        return HealthOut(status="ok", db=db_ok, storage=state.cfg.service.storage.type)

    @app.get("/api/procurements", response_model=ProcurementListOut)
    async def list_procurements(
        number: str | None = None,
        source_platform: str | None = None,
        okpd2: str | None = None,
        customer: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> ProcurementListOut:
        rows, total = await _repo().list_procurements(
            number=number,
            source_platform=source_platform,
            okpd2=okpd2,
            customer=customer,
            limit=limit,
            offset=offset,
        )
        return ProcurementListOut(
            total=total, items=[ProcurementOut.model_validate(r) for r in rows]
        )

    @app.get("/api/procurements/{procurement_id}", response_model=ProcurementDetailOut)
    async def get_procurement(procurement_id: int) -> ProcurementDetailOut:
        row = await _repo().get_by_id(procurement_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return ProcurementDetailOut.model_validate(row)

    @app.post(
        "/api/procurements/{procurement_id}/score",
        response_model=ProcurementDetailOut,
    )
    async def set_score(procurement_id: int, body: ScoreUpdate) -> ProcurementDetailOut:
        """Обновление score внешним сервисом по его инициативе."""
        if await _repo().get_by_id(procurement_id) is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        await _repo().update_score(procurement_id, body.score, body.score_method)
        row = await _repo().get_by_id(procurement_id)
        return ProcurementDetailOut.model_validate(row)

    @app.post(
        "/api/procurements/{procurement_id}/technical-spec",
        response_model=ProcurementDetailOut,
    )
    async def set_technical_spec(
        procurement_id: int, body: TechnicalSpecUpdate
    ) -> ProcurementDetailOut:
        """Обновление метаданных ТЗ внешним сервисом обработки файлов.

        Внешний сервис скачивает файлы (в т.ч. извлекает ТЗ из ZIP-архивов) и
        возвращает имя/URL файла технического задания.
        """
        if await _repo().get_by_id(procurement_id) is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        await _repo().update_technical_spec(procurement_id, name=body.name, url=body.url)
        row = await _repo().get_by_id(procurement_id)
        return ProcurementDetailOut.model_validate(row)

    @app.get("/api/procurements/{procurement_id}/technical-spec")
    async def download_technical_spec(procurement_id: int) -> Response:
        row = await _repo().get_by_id(procurement_id)
        if row is None or not row.technical_spec_url:
            raise HTTPException(status_code=404, detail="ТЗ не найдено")

        ts_url = row.technical_spec_url
        # В проде файлы лежат в S3/MinIO и доступны по URL напрямую.
        if ts_url.startswith("http"):
            return Response(status_code=302, headers={"Location": ts_url})

        # Локальное сохранение — только для отладки: читаем файл по пути.
        try:
            data = Path(ts_url).read_bytes()
        except OSError as exc:
            logger.error("Не удалось прочитать файл ТЗ %s: %s", ts_url, exc)
            raise HTTPException(status_code=404, detail="Файл ТЗ недоступен") from exc

        filename = ts_url.rsplit("/", 1)[-1]
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        # RFC 5987: не-ASCII имена в filename* (ASCII-имя в filename как запасной вариант)
        quoted = urlparse.quote(filename)
        disposition = f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"
        return Response(
            content=data,
            media_type=media_type,
            headers={"Content-Disposition": disposition},
        )

    return app
