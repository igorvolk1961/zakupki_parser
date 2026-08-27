"""Эндпоинты закупок: список, карточка, CSV-экспорт, скоринг и пакетная обработка."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from scoring_common.tz import extract_text_cached, find_tz_reference_cached
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
from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.parser.detail import extract_details
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)

# Лимит одновременных извлечений текста ТЗ: каждый запрос делает тяжёлую
# блокирующую работу (скачивание до 20 МБ, листинг архивов, конвертация docx/pdf)
# в потоках asyncio. Семафор ограничивает число таких операций в момент времени,
# чтобы всплеск запросов не исчерпал общий thread-pool приложения.
_TZ_EXTRACT_CONCURRENCY = 4
_tz_extract_semaphore = asyncio.Semaphore(_TZ_EXTRACT_CONCURRENCY)

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


async def _fetch_details_for_score(state: Any, row: Any) -> None:
    """Досборка деталей площадки ПОСЛЕ получения результата скоринга (BR-08).

    Вызывается из обработчика ``POST /api/procurements/{id}/score`` ПЕРЕД записью
    результата в БД: детали (ОКПД2/файлы/ИНН/статус/НМЦК) догружаются через
    единый интерфейс ``extract_details`` с браузерной страницей — одинаково для
    API-площадок (``fetch_api_details``) и DOM-площадок (детальная страница).
    Контекст запроса деталей (``detail_api``: need_id и т.п.) был сохранён при
    персисте на уровне списка. Сбой деталей (напр. HTTP 402 от API mos.ru) НЕ
    роняет обработчик скоринга: карточка остаётся на уровне списка, результат
    скоринга всё равно записывается.
    """
    if state.repository is None:
        return
    platform = (state.cfg.dom.platforms or {}).get(row.platform_id)
    if platform is None:
        return
    d = platform.detail
    # Нет источника деталей (ни API, ни DOM) — досборка не нужна.
    if not (d.api_format or d.variables or d.files or d.additional_pages):
        return
    browser = BrowserManager(state.cfg.parser.browser)
    try:
        await browser.start()
        page = await browser.new_page()
        try:
            detail_vars, files, api_inn = await extract_details(
                page, platform, {"number": row.number}, row.url, row.detail_api
            )
        finally:
            await browser.save_session()
        record = dict(row.detail_json or {})
        # Не затираем значения уровня списка значением None (например, НМЦК,
        # если API не отдал поле) — как в основном пути парсера.
        record.update({k: v for k, v in detail_vars.items() if v is not None})
        if files:
            record["files_json"] = files
        if api_inn and not record.get("inn"):
            record["inn"] = api_inn
        record["detail_json"] = json_safe(record)
        await state.repository.update_details(row.id, record)
        logger.info(
            "Закупка %s: детали площадки догружены перед записью результата скоринга",
            row.number,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Закупка %s: детали площадки не догружены (карточка остаётся на уровне списка): %s",
            row.number,
            exc,
        )
    finally:
        await browser.close()


def build_procurements_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    _active_context = ctx._active_context
    require_user = ctx.require_user
    require_base = ctx.require_base
    require_internal = ctx.require_internal
    require_user_or_internal = ctx.require_user_or_internal

    @router.get(
        "/api/procurements",
        response_model=ProcurementListOut,
        dependencies=[Depends(require_base)],
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
        assert profile is not None
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
        dependencies=[Depends(require_base)],
    )
    async def export_procurements(
        body: ExportIn | None = None, user: User | None = Depends(require_base)
    ) -> Response:
        """Выгружает активные релевантные закупки в CSV (скачивание в браузер).

        В выгрузку попадают ТОЛЬКО активные (по статусу и сроку актуальности) и
        релевантные (прошедшие внешний скоринг с fit_score >= порога) закупки —
        как фильтр «Только релевантные» в таблице закупок. Порог по умолчанию 0.4.
        Файл отдаётся клиенту (браузер сам предложит выбрать папку), на сервере
        ничего не пишется. Операция read-only — безопасна при работающем парсере.
        """
        threshold = body.min_fit_score if body is not None else 0.4
        _, profile = await _active_context(user)
        assert profile is not None
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

        logger.info("Выгружено закупок в CSV (клиент): %s", len(rows))
        return Response(
            content=buf.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="procurements.csv"',
            },
        )

    @router.get(
        "/api/procurements/{procurement_id}",
        response_model=ProcurementDetailOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def get_procurement(
        procurement_id: int, user: User | None = Depends(require_user_or_internal)
    ) -> ProcurementDetailOut:
        _, profile = await _active_context(user)
        row = await _repo().get_by_id(
            procurement_id, profile_id=profile.id if profile is not None else None
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return _procurement_detail_out(row)

    @router.get(
        "/api/procurements/{procurement_id}/tz",
        dependencies=[Depends(require_user_or_internal)],
    )
    async def get_procurement_tz(
        procurement_id: int, user: User | None = Depends(require_user_or_internal)
    ) -> dict[str, Any]:
        """Текст ТЗ закупки (Markdown, в т.ч. из архива) для просмотра в карточке.

        Используется та же логика, что и конвейером скоринга (``scoring_common.tz``):
        прямой файл ТЗ → поиск внутри архивов (zip/tar) → извлечение docx/pdf.
        Текст кэшируется (``extract_text_cached``): при повторном открытии карточки
        файл заново не скачивается и не конвертируется.
        """
        _, profile = await _active_context(user)
        row = await _repo().get_by_id(
            procurement_id, profile_id=profile.id if profile is not None else None
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        record = {"files_json": row.files_json or []}
        # Тяжёлые блокирующие операции выполняем в потоке, но ограничиваем их
        # число семафором (см. _TZ_EXTRACT_CONCURRENCY): холодный кэш не должен
        # насыщать общий thread-pool одновременными скачиваниями/конвертациями.
        # find_tz_reference_cached кэширует результат поиска (в т.ч. листинг
        # архивов) — при повторном открытии карточки архивы заново не скачиваются.
        async with _tz_extract_semaphore:
            ref = await asyncio.to_thread(find_tz_reference_cached, record, 30.0)
            if ref is None:
                return {"found": False, "file_name": None, "from_archive": False, "text": None}
            text = await asyncio.to_thread(extract_text_cached, ref, 30.0)
        return {
            "found": text is not None,
            "file_name": ref.name,
            "from_archive": "#" in ref.url,
            "text": text,
        }

    @router.post(
        "/api/procurements/{procurement_id}/score",
        response_model=ProcurementDetailOut,
        dependencies=[Depends(require_internal)],
    )
    async def set_score(procurement_id: int, body: ScoreUpdate) -> ProcurementDetailOut:
        """Обновление score внешним сервисом по его инициативе.

        Результат пишется профилю из ``body.profile_id`` (пер-профильно, BR-07):
        скор привязан к компетенциям конкретного профиля, поэтому «раздача одного
        скора всем профилям-участникам» не используется. Автокаскад Fit -> P(win)
        -> Margin отключён: P(win)/Margin вычисляются только по явному запросу
        тендеролога.

        RAG-отчёт (``rag_report``) сохраняется отдельно и не меняет score_method.
        Уведомляет подписчиков ПОСЛЕ стадии (fit/pwin/margin), когда результат
        стадии изменён и прошёл её порог (ADR-7).
        """
        existing = await _repo().get_by_id(procurement_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        logger.info(
            "Получен результат скоринга закупки %s (профиль %s): score=%s method=%s fit=%s",
            procurement_id,
            body.profile_id,
            body.score,
            body.score_method,
            body.fit_score,
        )
        # BR-08: детали площадки догружаются ПОСЛЕ получения результата скоринга,
        # НО ПЕРЕД записью результата в БД — единый интерфейс fetch_api_details
        # (лёгкий APIRequestContext, без DOM/браузера).
        await _fetch_details_for_score(state, existing)
        if body.rag_report is not None:
            # Анализ стоп-условий: сохраняем отчёт профилю (score_method не меняем).
            await _repo().update_rag_report(procurement_id, body.profile_id, body.rag_report)
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
                body.profile_id,
                score=body.score,
                fit_score=body.fit_score,
                p_win=body.p_win,
                margin=body.margin,
                score_method=body.score_method,
                embedding_similarity=body.embedding_similarity,
                langfuse_trace_url=body.langfuse_trace_url,
            )
            # BR-07 (дедупликация по содержанию компетенций): результат, посчитанный
            # для представителя группы идентичного содержания компетенций,
            # распространяется на всех профилей, отобравших эту закупку с тем же
            # comp_hash (подписка). Один LLM-вызов на группу, результат — у всех.
            try:
                rep = await _repo().get_score(procurement_id, body.profile_id)
                if rep is not None and rep.comp_hash:
                    await _repo().apply_score_to_comp_hash_group(
                        procurement_id,
                        rep.comp_hash,
                        score=body.score,
                        fit_score=body.fit_score,
                        p_win=body.p_win,
                        margin=body.margin,
                        score_method=body.score_method,
                        embedding_similarity=body.embedding_similarity,
                        langfuse_trace_url=body.langfuse_trace_url,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Не удалось распространить результат скоринга на группу "
                    "компетенций закупки %s: %s",
                    procurement_id,
                    exc,
                )
        await _broadcast(state)
        row = await _repo().get_by_id(procurement_id, profile_id=body.profile_id)
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
        dependencies=[Depends(require_base)],
    )
    async def analyze_procurements(
        body: ProcurementIdsIn, user: User | None = Depends(require_base)
    ) -> dict[str, Any]:
        """Обработать выбранные закупки: авто-Fit (если нет) + RAG-анализ ТЗ.

        Внутренние стадии скрыты от заказчика: для каждой закупки ставится
        задание fit (если per-profile fit ещё не посчитан) и затем analysis.
        Ручная корректировка оценок — вне MVP (Эпик 5, пост-MVP).
        """
        if state.score_transport is None:
            raise HTTPException(status_code=409, detail="Транспорт скоринга не настроен")
        _, profile = await _active_context(user)
        assert profile is not None
        queued: list[int] = []
        for procurement_id in body.procurement_ids:
            current = await _repo().get_score(procurement_id, profile.id)
            if current is None or current.fit_score is None:
                await _enqueue_next_stage(state, procurement_id, "fit", 0.5, profile.id)
            await _enqueue_next_stage(state, procurement_id, "analysis", 0.5, profile.id)
            queued.append(procurement_id)
        logger.info("Поставлено на обработку (fit+analysis): %s", queued)
        return {"status": "queued", "procurement_ids": queued}

    @router.post(
        "/api/procurements/pwin-margin",
        include_in_schema=False,
        dependencies=[Depends(require_base)],
    )
    async def pwin_margin_procurements(
        body: ProcurementIdsIn, user: User | None = Depends(require_base)
    ) -> dict[str, Any]:
        """Оценить P(win) и Margin для выбранных закупок (on-demand, обе стадии)."""
        if state.score_transport is None:
            raise HTTPException(status_code=409, detail="Транспорт скоринга не настроен")
        cfg = state.cfg.score
        _, profile = await _active_context(user)
        assert profile is not None
        queued: list[int] = []
        for procurement_id in body.procurement_ids:
            if cfg.pwin_enabled:
                await _enqueue_next_stage(state, procurement_id, "pwin", 0.5, profile.id)
            if cfg.margin_enabled:
                await _enqueue_next_stage(state, procurement_id, "margin", 0.5, profile.id)
            queued.append(procurement_id)
        logger.info("Поставлено на оценку P(win)/Margin: %s", queued)
        return {"status": "queued", "procurement_ids": queued}

    return router
