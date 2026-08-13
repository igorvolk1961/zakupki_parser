"""FastAPI-сервис: чтение закупок из БД, web-демо и управление парсером."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import parse as urlparse

import yaml
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text as sql_text

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig, ServiceConfig
from zakupki_parser.notify import Notifier
from zakupki_parser.storage.db import Database, Procurement
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
    customer_id: int | None = None
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
    fit_score: float | None = None
    score_method: str | None = None
    embedding_similarity: float | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ProcurementDetailOut(ProcurementOut):
    """Карточка закупки с полным detail_json."""

    detail_json: dict[str, Any] | None = None


class ProcurementListOut(BaseModel):
    total: int
    items: list[ProcurementOut]


class CustomerOut(BaseModel):
    """Карточка заказчика (ADR-4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    inn: str | None = None
    rating: float | None = None
    created_at: datetime
    updated_at: datetime


class CustomerListOut(BaseModel):
    total: int
    items: list[CustomerOut]


class RatingUpdate(BaseModel):
    """Установка рейтинга заказчика внешним сервисом (ADR-4)."""

    rating: float


class HealthOut(BaseModel):
    status: str
    db: bool


class ScoreUpdate(BaseModel):
    """Обновление score внешним сервисом (по его инициативе)."""

    score: float
    fit_score: float | None = None
    score_method: str = "external"
    embedding_similarity: float | None = None


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
    def __init__(self, cfg: AppConfig, configs_dir: str) -> None:
        self.cfg = cfg
        self.configs_dir = configs_dir
        self.db: Database | None = None
        self.repository: ProcurementRepository | None = None
        # Управление парсером (запуск/остановка из web-демо).
        self.parser_lock = asyncio.Lock()
        self.parser_task: asyncio.Task[None] | None = None
        self.parser_status: dict[str, Any] = {
            "running": False,
            "stopped": False,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }
        # WebSocket-клиенты web-демо (живые обновления при изменении БД).
        self.ws_clients: set[WebSocket] = set()
        # Отложенное пороговое уведомление (ADR-7): Notifier + порог fit_score,
        # используется в POST /score.
        self.notifier: Notifier | None = None
        self.notify_min_fit_score: float = 0.0


async def _broadcast(state: AppState, message: str = "data-changed") -> None:
    """Оповещает подключённых клиентов web-демо об изменении данных."""
    for ws in list(state.ws_clients):
        try:
            await ws.send_text(message)
        except Exception:  # noqa: BLE001
            state.ws_clients.discard(ws)


async def _run_parser(state: AppState) -> None:
    """Запускает постоянный мониторинг парсера (периодические проходы) в фоне."""
    from zakupki_parser.scheduler import Scheduler

    scheduler = Scheduler(state.cfg, on_update=lambda: _broadcast(state))
    try:
        await scheduler.run_service()
    except asyncio.CancelledError:
        # Остановка по команде пользователя — это не ошибка.
        state.parser_status["stopped"] = True
        state.parser_status["error"] = None
    except Exception as exc:  # noqa: BLE001
        state.parser_status["error"] = str(exc)
    finally:
        with suppress(Exception):
            await scheduler.stop()
        await _broadcast(state)
        state.parser_status["running"] = False
        state.parser_status["finished_at"] = datetime.now(UTC).isoformat()
        state.parser_task = None


def _create_state(configs_dir: str) -> AppState:
    cfg = load_config(configs_dir)
    return AppState(cfg, configs_dir)


def _procurement_out(row: Procurement) -> ProcurementOut:
    """Карточка закупки с именем заказчика (имя — из связи customers, не колонки)."""
    out = ProcurementOut.model_validate(row)
    out.customer_id = row.customer_id
    out.customer = row.customer_rel.name if row.customer_rel is not None else None
    return out


def _procurement_detail_out(row: Procurement) -> ProcurementDetailOut:
    out = ProcurementDetailOut.model_validate(row)
    out.customer_id = row.customer_id
    out.customer = row.customer_rel.name if row.customer_rel is not None else None
    return out


def _row_to_record(row: Procurement) -> dict[str, Any]:
    """Карточка закупки как dict для уведомлений (поля, понятные Notifier)."""
    return {
        "number": row.number,
        "source_platform": row.source_platform,
        "url": row.url,
        "customer": row.customer_rel.name if row.customer_rel is not None else None,
        "law": row.law,
        "subject": row.subject,
        "nmck": row.nmck,
        "publication_date": row.publication_date,
        "deadline": row.deadline,
        "score": row.score,
        "fit_score": row.fit_score,
        "score_method": row.score_method,
        "embedding_similarity": row.embedding_similarity,
        "is_active": row.is_active,
    }


def create_app(configs_dir: str = "configs") -> FastAPI:
    state = _create_state(configs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(state.cfg.ops.db)
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

    demo_html = Path(__file__).parent / "demo.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def demo() -> str:
        """Простое web-приложение для демонстрации MVP (читает данные через API)."""
        return demo_html.read_text(encoding="utf-8")

    def _repo() -> ProcurementRepository:
        if state.repository is None:
            raise HTTPException(status_code=503, detail="БД недоступна")
        return state.repository

    # Уведомления подписчиков — отправляются в POST /score после прихода внешнего
    # скора и прохождения порога notify_min_fit_score (ADR-7).
    state.notifier = Notifier(state.cfg.ops.notifications)
    state.notify_min_fit_score = state.cfg.ops.notifications.notify_min_fit_score

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
        return HealthOut(status="ok", db=db_ok)

    @app.get("/api/procurements", response_model=ProcurementListOut)
    async def list_procurements(
        number: str | None = None,
        source_platform: str | None = None,
        okpd2: str | None = None,
        customer: str | None = None,
        active: bool | None = None,
        min_fit_score: float | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> ProcurementListOut:
        rows, total = await _repo().list_procurements(
            number=number,
            source_platform=source_platform,
            okpd2=okpd2,
            customer=customer,
            active=active,
            min_fit_score=min_fit_score,
            limit=limit,
            offset=offset,
        )
        return ProcurementListOut(total=total, items=[_procurement_out(r) for r in rows])

    # Плоские колонки для CSV-выгрузки (без detail_json/files_json).
    CSV_COLUMNS = [
        "id",
        "number",
        "source_platform",
        "url",
        "customer",
        "law",
        "subject",
        "nmck",
        "publication_date",
        "update_date",
        "deadline",
        "execution_term",
        "okpd2_codes",
        "kpgz_codes",
        "security_amount",
        "security_amount_unit",
        "advance",
        "technical_spec_url",
        "technical_spec_name",
        "score",
        "fit_score",
        "score_method",
        "is_active",
    ]

    @app.post("/api/procurements/export", include_in_schema=False)
    async def export_procurements() -> dict[str, Any]:
        """Выгружает закупки из БД в CSV на сервере (каталог export_dir).

        Файл пишется в ``config_service.yaml -> export_dir`` (создаётся при
        необходимости). Операция read-only — безопасна при работающем парсере.
        """
        rows, _ = await _repo().list_procurements(limit=10**9)

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = _procurement_out(row).model_dump()
            for col in ("publication_date", "update_date", "deadline"):
                if isinstance(out.get(col), datetime):
                    out[col] = out[col].isoformat()
            writer.writerow(out)

        export_dir = Path(state.cfg.ops.export_dir)
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            target = export_dir / "procurements.csv"
            target.write_bytes(buf.getvalue().encode("utf-8-sig"))
        except OSError as exc:
            logger.error("Не удалось записать CSV %s: %s", export_dir, exc)
            raise HTTPException(status_code=500, detail=f"Не удалось выгрузить CSV: {exc}") from exc

        logger.info("Выгружено закупок в CSV: %s -> %s", len(rows), target)
        return {"status": "exported", "count": len(rows), "path": str(target)}

    @app.get("/api/procurements/{procurement_id}", response_model=ProcurementDetailOut)
    async def get_procurement(procurement_id: int) -> ProcurementDetailOut:
        row = await _repo().get_by_id(procurement_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return _procurement_detail_out(row)

    @app.post(
        "/api/procurements/{procurement_id}/score",
        response_model=ProcurementDetailOut,
    )
    async def set_score(procurement_id: int, body: ScoreUpdate) -> ProcurementDetailOut:
        """Обновление score внешним сервисом по его инициативе.

        После обновления score уведомляет подписчиков, если
        fit_score >= notify_min_fit_score (отложенное пороговое уведомление, ADR-7).
        """
        if await _repo().get_by_id(procurement_id) is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        await _repo().update_score(
            procurement_id,
            body.score,
            body.fit_score,
            body.score_method,
            embedding_similarity=body.embedding_similarity,
        )
        await _broadcast(state)
        row = await _repo().get_by_id(procurement_id)
        if row is None:  # pragma: no cover - проверено выше
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        if (
            state.notifier is not None
            and row.fit_score is not None
            and row.fit_score >= state.notify_min_fit_score
        ):
            await state.notifier.notify(_row_to_record(row))
        return _procurement_detail_out(row)

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
        await _broadcast(state)
        row = await _repo().get_by_id(procurement_id)
        if row is None:  # pragma: no cover - проверено выше
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return _procurement_detail_out(row)

    @app.get("/api/procurements/{procurement_id}/technical-spec")
    async def download_technical_spec(procurement_id: int) -> Response:
        row = await _repo().get_by_id(procurement_id)
        if row is None or not row.technical_spec_url:
            raise HTTPException(status_code=404, detail="ТЗ не найдено")

        ts_url = row.technical_spec_url
        # Парсер не скачивает файлы: technical_spec_url — URL скачивания с ЭТП,
        # поэтому обычно делаем редирект на оригинал.
        if ts_url.startswith("http"):
            return Response(status_code=302, headers={"Location": ts_url})

        # Локальный путь возможен в старых данных (до отказа от скачивания).
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

    @app.get("/api/customers", response_model=CustomerListOut)
    async def list_customers(
        name: str | None = None,
        inn: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> CustomerListOut:
        rows, total = await _repo().list_customers(name=name, inn=inn, limit=limit, offset=offset)
        return CustomerListOut(total=total, items=[CustomerOut.model_validate(r) for r in rows])

    @app.get("/api/customers/{customer_id}", response_model=CustomerOut)
    async def get_customer(customer_id: int) -> CustomerOut:
        row = await _repo().get_customer(customer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Заказчик не найден")
        return CustomerOut.model_validate(row)

    @app.post("/api/customers/{customer_id}/rating", response_model=CustomerOut)
    async def set_customer_rating(customer_id: int, body: RatingUpdate) -> CustomerOut:
        """Установка рейтинга заказчика внешним сервисом (ADR-4)."""
        if not await _repo().set_customer_rating(customer_id, body.rating):
            raise HTTPException(status_code=404, detail="Заказчик не найден")
        row = await _repo().get_customer(customer_id)
        return CustomerOut.model_validate(row)

    @app.websocket("/ws")
    async def ws_updates(websocket: WebSocket) -> None:
        """Канал живых обновлений: шлёт 'data-changed' при изменении БД."""
        await websocket.accept()
        state.ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.ws_clients.discard(websocket)

    @app.get("/api/parser/status", include_in_schema=False)
    async def parser_status() -> dict[str, Any]:
        """Текущее состояние парсера (запущен/остановлен, ошибка, время)."""
        status = dict(state.parser_status)
        if state.parser_task is not None and not state.parser_task.done():
            status["running"] = True
        return status

    @app.post("/api/parser/start", include_in_schema=False)
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

    @app.post("/api/parser/stop", include_in_schema=False)
    async def stop_parser() -> dict[str, Any]:
        """Останавливает запущенный проход парсера."""
        task = state.parser_task
        if task is None or task.done():
            return {"status": "idle"}
        task.cancel()
        logger.info("Запрошена остановка парсера из web-демо")
        return {"status": "stopping"}

    @app.post("/api/db/clear", include_in_schema=False)
    async def clear_db() -> dict[str, Any]:
        """Очищает БД (закупки и заказчики). Доступно только при остановленном парсере."""
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        deleted = await _repo().clear_all()
        logger.info("БД очищена из web-демо: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    # ------------------------------------------------------------------ #
    # Конфигурация сервиса (config_service.yaml) — просмотр/редактирование
    # ------------------------------------------------------------------ #
    @app.get("/api/config/threshold", response_model=dict[str, Any], include_in_schema=False)
    async def get_relevance_threshold() -> dict[str, Any]:
        """Порог релевантности (fit_score) — используется переключателем «Только релевантные».

        Значение берётся из config_ops.yaml (notifications.notify_min_fit_score),
        эксплуатационные параметры целиком через API не отдаются.
        """
        return {"notify_min_fit_score": state.cfg.ops.notifications.notify_min_fit_score}

    @app.get("/api/config", response_model=dict[str, Any], include_in_schema=False)
    async def get_config() -> dict[str, Any]:
        """Текущие параметры config_service.yaml (аналитические настройки).

        Секреты и эксплуатационные параметры (БД, уведомления, таймер) живут в
        config_ops.yaml и не редактируются через этот API.
        """
        return state.cfg.service.model_dump()

    @app.put("/api/config", response_model=dict[str, Any], include_in_schema=False)
    async def put_config(body: dict[str, Any]) -> dict[str, Any]:
        """Валидирует и сохраняет аналитические параметры config_service.yaml.

        Эксплуатационные параметры (БД, уведомления, секреты) не редактируются
        через API — они живут в config_ops.yaml и берутся из env.
        """
        try:
            new_service = ServiceConfig.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        target = Path(state.configs_dir) / "config_service.yaml"
        try:
            target.write_text(
                yaml.safe_dump(
                    new_service.model_dump(exclude_none=True),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            detail = f"Не удалось записать конфиг: {exc}"
            raise HTTPException(status_code=500, detail=detail) from exc
        state.cfg.service = new_service
        logger.info("Сохранён config_service.yaml (%s)", target)
        return new_service.model_dump()

    return app
