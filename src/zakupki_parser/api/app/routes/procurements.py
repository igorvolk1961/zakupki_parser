"""Эндпоинты закупок: список, карточка, CSV-экспорт, скоринг и пакетная обработка."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from zakupki_parser.api.app.converters import (
    _meets_stage_notify_threshold,
    _procurement_detail_out,
    _procurement_out,
    _row_to_record,
)
from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    ExportIn,
    ProcurementDetailOut,
    ProcurementIdsIn,
    ProcurementListOut,
    ScoreUpdate,
)
from zakupki_parser.api.app.state import _broadcast, _enqueue_next_stage
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)

# Плоские колонки для CSV-выгрузки (без detail_json/files_json).
CSV_COLUMNS = [
    "id",
    "number",
    "platform_id",
    "url",
    "customer",
    "procedure_type",
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
    "score",
    "fit_score",
    "p_win",
    "margin",
    "score_method",
    "is_active",
]


def build_procurements_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    _active_context = ctx._active_context
    require_user = ctx.require_user
    require_internal = ctx.require_internal
    require_user_or_internal = ctx.require_user_or_internal

    @router.get(
        "/api/procurements",
        response_model=ProcurementListOut,
        dependencies=[Depends(require_user)],
    )
    async def list_procurements(
        number: str | None = None,
        platform_id: str | None = None,
        okpd2: str | None = None,
        customer: str | None = None,
        active: bool | None = None,
        min_fit_score: float | None = None,
        scored: bool | None = None,
        sort: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        user: User | None = Depends(require_user),
    ) -> ProcurementListOut:
        # Per-profile скоринг активного профиля пользователя (BR-07).
        _, profile = await _active_context(user)
        rows, total = await _repo().list_procurements(
            number=number,
            platform_id=platform_id,
            okpd2=okpd2,
            customer=customer,
            active=active,
            min_fit_score=min_fit_score,
            scored=scored,
            sort=sort,
            limit=limit,
            offset=offset,
            profile_id=profile.id,
        )
        return ProcurementListOut(total=total, items=[_procurement_out(r) for r in rows])

    @router.post(
        "/api/procurements/export",
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def export_procurements(
        body: ExportIn | None = None, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Выгружает активные релевантные закупки из БД в CSV (каталог export_dir).

        В выгрузку попадают ТОЛЬКО активные (по статусу и сроку актуальности) и
        релевантные (прошедшие внешний скоринг с fit_score >= порога) закупки —
        как фильтр «Только релевантные» в таблице закупок. Порог по умолчанию 0.4.

        Файл пишется в ``config_service.yaml -> export_dir`` (создаётся при
        необходимости). Операция read-only — безопасна при работающем парсере.
        """
        threshold = body.min_fit_score if body is not None else 0.4
        _, profile = await _active_context(user)
        rows, _ = await _repo().list_procurements(
            active=True,
            min_fit_score=threshold,
            limit=10**9,
            profile_id=profile.id,
        )

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

    @router.get(
        "/api/procurements/{procurement_id}",
        response_model=ProcurementDetailOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def get_procurement(
        procurement_id: int, user: User | None = Depends(require_user_or_internal)
    ) -> ProcurementDetailOut:
        _, profile = await _active_context(user)
        row = await _repo().get_by_id(procurement_id, profile_id=profile.id)
        if row is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return _procurement_detail_out(row)

    @router.post(
        "/api/procurements/{procurement_id}/score",
        response_model=ProcurementDetailOut,
        dependencies=[Depends(require_internal)],
    )
    async def set_score(procurement_id: int, body: ScoreUpdate) -> ProcurementDetailOut:
        """Обновление score внешним сервисом по его инициативе.

        Результат пишется в per-user скоринг (``procurement_evaluations``) сервис-аккаунта;
        базовые колонки ``procurements`` обновляются для совместимости (дефолтный скор).
        Автокаскад Fit -> P(win) -> Margin отключён: P(win)/Margin вычисляются только по
        явному запросу тендеролога.

        RAG-отчёт (``rag_report``) сохраняется отдельно и не меняет score_method.
        Уведомляет подписчиков ПОСЛЕ стадии (fit/pwin/margin), когда результат
        стадии изменён и прошёл её порог (ADR-7).
        """
        existing = await _repo().get_by_id(procurement_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        # Внутренний вызов конвейера: результат пишется под активный профиль
        # сервис-аккаунта (оценки относятся к профилю, BR-07).
        _, profile = await _active_context(None)
        logger.info(
            "Получен результат скоринга закупки %s: score=%s method=%s fit=%s",
            procurement_id,
            body.score,
            body.score_method,
            body.fit_score,
        )
        if body.rag_report is not None:
            # Анализ стоп-условий: сохраняем отчёт, результат скоринга не меняем.
            await _repo().update_rag_report(procurement_id, profile.id, body.rag_report)
        # Результат стадии каскада (fit/pwin/margin/sim) применяется и вместе с
        # rag_report: rag_report не отменяет скоринг. Чисто аналитический результат
        # (rag_report без fit_score/p_win/margin) скоринг не трогает — у analysis-воркера
        # score=0.0 — это заглушка, перезаписывать ею оценку нельзя.
        has_stage_result = (
            body.fit_score is not None or body.p_win is not None or body.margin is not None
        )
        if body.rag_report is None or has_stage_result:
            await _repo().upsert_score(
                procurement_id,
                profile.id,
                score=body.score,
                fit_score=body.fit_score,
                p_win=body.p_win,
                margin=body.margin,
                score_method=body.score_method,
                embedding_similarity=body.embedding_similarity,
            )
        await _broadcast(state)
        row = await _repo().get_by_id(procurement_id, profile_id=profile.id)
        if row is None:  # pragma: no cover - проверено выше
            raise HTTPException(status_code=404, detail="Закупка не найдена")

        # Уведомление после стадии: только когда результат стадии изменён
        # (не повторная доставка) и возвращаемое значение прошло её порог.
        stage_changed = existing.score_method != row.score_method
        if (
            stage_changed
            and state.notifier is not None
            and _meets_stage_notify_threshold(row, state)
        ):
            await state.notifier.notify(_row_to_record(row))
        return _procurement_detail_out(row)

    @router.post(
        "/api/procurements/analyze",
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def analyze_procurements(
        body: ProcurementIdsIn, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Обработать выбранные закупки: авто-Fit (если нет) + RAG-анализ ТЗ.

        Внутренние стадии скрыты от заказчика: для каждой закупки ставится
        задание fit (если per-profile fit ещё не посчитан) и затем analysis.
        Ручная корректировка оценок — вне MVP (Эпик 5, пост-MVP).
        """
        if state.score_transport is None:
            raise HTTPException(status_code=409, detail="Транспорт скоринга не настроен")
        _, profile = await _active_context(user)
        queued: list[int] = []
        for procurement_id in body.procurement_ids:
            current = await _repo().get_score(procurement_id, profile.id)
            if current is None or current.fit_score is None:
                await _enqueue_next_stage(state, procurement_id, "fit", 0.5)
            await _enqueue_next_stage(state, procurement_id, "analysis", 0.5)
            queued.append(procurement_id)
        logger.info("Поставлено на обработку (fit+analysis): %s", queued)
        return {"status": "queued", "procurement_ids": queued}

    @router.post(
        "/api/procurements/pwin-margin",
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def pwin_margin_procurements(
        body: ProcurementIdsIn, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Оценить P(win) и Margin для выбранных закупок (on-demand, обе стадии)."""
        if state.score_transport is None:
            raise HTTPException(status_code=409, detail="Транспорт скоринга не настроен")
        cfg = state.cfg.score
        await _active_context(user)
        queued: list[int] = []
        for procurement_id in body.procurement_ids:
            if cfg.pwin_enabled:
                await _enqueue_next_stage(state, procurement_id, "pwin", 0.5)
            if cfg.margin_enabled:
                await _enqueue_next_stage(state, procurement_id, "margin", 0.5)
            queued.append(procurement_id)
        logger.info("Поставлено на оценку P(win)/Margin: %s", queued)
        return {"status": "queued", "procurement_ids": queued}

    return router
