"""Эндпоинты закупок: список, карточка, CSV-экспорт, скоринг и пакетная обработка."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from scoring_common.requirements import extract_requirements
from scoring_common.tz import resolve_tz_content_cached
from zakupki_parser.api.app.converters import (
    _meets_stage_notify_threshold,
    _procurement_detail_out,
    _procurement_out,
    _row_to_record,
)
from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    AcceptWorkByUrlIn,
    AcceptWorkIn,
    ExportIn,
    ProcurementDetailOut,
    ProcurementIdsIn,
    ProcurementListOut,
    RejectIn,
    RequirementsIn,
    RequirementsOut,
    ScoreUpdate,
    WorkItemOut,
    WorkItemsListOut,
)
from zakupki_parser.api.app.state import _broadcast, _enqueue_next_stage
from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.parser.detail import extract_detail_vars, extract_details, open_detail
from zakupki_parser.parser.filtering import region_match
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)

# Лимит одновременных извлечений текста ТЗ: каждый запрос делает тяжёлую
# блокирующую работу (скачивание до 20 МБ, листинг архивов, конвертация docx/pdf)
# в потоках asyncio. Семафор ограничивает число таких операций в момент времени,
# чтобы всплеск запросов не исчерпал общий thread-pool приложения.
_TZ_EXTRACT_CONCURRENCY = 4
_tz_extract_semaphore = asyncio.Semaphore(_TZ_EXTRACT_CONCURRENCY)

# Лимит одновременных извлечений требований к участнику: просмотр скачивает и
# конвертирует КАЖДЫЙ документ карточки (потенциально архив + десятки записей) в
# потоках asyncio — всплеск запросов не должен исчерпать общий thread-pool.
_REQ_EXTRACT_CONCURRENCY = 2
_req_extract_semaphore = asyncio.Semaphore(_REQ_EXTRACT_CONCURRENCY)

# Плоские колонки для CSV-выгрузки (без detail_json/files_json).
CSV_COLUMNS = [
    "id",
    "number",
    "platform_id",
    "url",
    "customer",
    "procedure_type",
    "law",
    "region",
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


async def _fetch_details_for_score(state: Any, row: Any, *, need_region: bool = False) -> None:
    """Досборка деталей площадки ПОСЛЕ получения результата скоринга (BR-08).

    Вызывается из обработчика ``POST /api/procurements/{id}/score`` ПЕРЕД записью
    результата в БД: детали (ОКПД2/файлы/ИНН/статус/НМЦК) догружаются через
    единый интерфейс ``extract_details`` с браузерной страницей — одинаково для
    API-площадок (``fetch_api_details``) и DOM-площадок (детальная страница).
    Контекст запроса деталей (``detail_api``: need_id и т.п.) был сохранён при
    персисте на уровне списка. Сбой деталей (напр. HTTP 402 от API mos.ru) НЕ
    роняет обработчик скоринга: карточка остаётся на уровне списка, результат
    скоринга всё равно записывается.

    ``need_region`` — регион запрошен профилем (target_regions/расстояние): если
    у площадки регион отсутствует в API-деталях, но доступен на DOM-странице
    (``detail.region_on_demand_dom``, напр. gz lot-online 44-ФЗ — поле
    «Место поставки»), открываем страницу и извлекаем регион. Иначе DOM-страница
    не открывается (такие площадки в обычном потоке работают без браузера).
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
        # Регион «по требованию»: у API-площадок (gz lot-online 44) региона в API нет,
        # но профиль явно запросил регион (need_region) — открываем common-страницу
        # (row.url) и забираем «Место поставки» из DOM (detail.region_on_demand_dom).
        if (
            need_region
            and not (record.get("region") or (row.region or ""))
            and d.region_on_demand_dom
            and row.url
        ):
            try:
                await open_detail(page, row.url, platform)
                dom_region = (await extract_detail_vars(page, platform)).get("region")
                if dom_region:
                    record["region"] = str(dom_region)
                    logger.info(
                        "Закупка %s: регион из DOM-страницы по запросу профиля: %s",
                        row.number,
                        dom_region,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Закупка %s: не удалось извлечь регион из DOM-страницы: %s",
                    row.number,
                    exc,
                )
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


def _is_analyst(user: User | None) -> bool:
    """Роль analyst: стоимость обработки — внутренняя метрика analyst-роли."""
    return bool(user is not None and "analyst" in (user.roles or []))


def _procurement_region(row: Any) -> str:
    """Регион закупки: колонка ``region`` или снимок ``detail_json`` (если колонка пуста)."""
    if row is None:
        return ""
    value = row.region
    if isinstance(value, str):
        return value
    detail = row.detail_json or {}
    value = detail.get("region")
    return value if isinstance(value, str) else ""


def _region_string_filter_enabled(profile: Any) -> bool:
    """Строковая пост-фильтрация по региону активна для профиля.

    Активна, если заданы целевые регионы и НЕ задан ``max_region_distance_km``:
    при заданном расстоянии решение принимается только на этапе анализа
    (парсер строковый регион не проверяет).
    """
    return bool(profile is not None and profile.target_regions) and not bool(
        getattr(profile, "max_region_distance_km", None)
    )


def _region_explicitly_requested(profile: Any) -> bool:
    """Профиль явно запросил регион (целевые регионы ИЛИ макс. расстояние, км).

    Явный запрос включает досборку региона из DOM для площадок, где в API-деталях
    региона нет (gz lot-online: поле «Место поставки» на common-странице) — см.
    ``detail.region_on_demand_dom`` и ``_fetch_details_for_score``.
    """
    return bool(
        profile is not None
        and (profile.target_regions or getattr(profile, "max_region_distance_km", None) is not None)
    )


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
        region: str | None = None,
        active: bool | None = None,
        min_fit_score: float | None = None,
        scored: bool | None = None,
        include_rejected: bool | None = None,
        in_work: bool | None = None,
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
            region=region,
            active=active,
            min_fit_score=min_fit_score,
            scored=scored,
            include_rejected=bool(include_rejected),
            in_work=in_work,
            sort=sort,
            limit=limit,
            offset=offset,
            profile_id=profile.id,
        )
        return ProcurementListOut(
            total=total,
            items=[_procurement_out(r, include_costs=_is_analyst(user)) for r in rows],
        )

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
            out = _procurement_out(row, include_costs=_is_analyst(user)).model_dump()
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

    @router.post(
        "/api/procurements/{procurement_id}/reject",
        response_model=ProcurementDetailOut,
        dependencies=[Depends(require_base)],
    )
    async def reject_procurement(
        procurement_id: int,
        body: RejectIn,
        user: User | None = Depends(require_base),
    ) -> ProcurementDetailOut:
        """Отбраковывает закупку активным профилем (Эпик 5, US-5.1/5.2).

        Закупка помечается ``status='rejected'`` (+ причина) и скрывается из
        выдачи (если не включён показ отклонённых). Опционально убирает из
        профиля ключевые слова, по которым закупка отобрана, и/или добавляет
        слово-исключение (явное действие пользователя).
        """
        _, profile = await _active_context(user)
        assert profile is not None
        existing = await _repo().get_by_id(procurement_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        await _repo().reject(
            procurement_id,
            profile.id,
            rejection_reason=body.rejection_reason,
            remove_matched_keywords=body.remove_matched_keywords,
            exclusion_word=body.exclusion_word,
        )
        await _broadcast(state)
        row = await _repo().get_by_id(procurement_id, profile_id=profile.id)
        if row is None:  # pragma: no cover - проверено выше
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return _procurement_detail_out(row, include_costs=_is_analyst(user))

    @router.get(
        "/api/procurements/work",
        response_model=WorkItemsListOut,
        dependencies=[Depends(require_base)],
    )
    async def list_work_items(
        user: User | None = Depends(require_base),
    ) -> WorkItemsListOut:
        """Закупки «в работе» активного профиля (вкладка «В работе»)."""
        _, profile = await _active_context(user)
        assert profile is not None
        items = await _repo().list_work_items(profile.id)
        return WorkItemsListOut(
            total=len(items),
            items=[WorkItemOut.model_validate(item) for item in items],
        )

    @router.post(
        "/api/procurements/{procurement_id}/work",
        response_model=WorkItemOut,
        dependencies=[Depends(require_base)],
    )
    async def accept_into_work(
        procurement_id: int,
        body: AcceptWorkIn | None = None,
        user: User | None = Depends(require_base),
    ) -> WorkItemOut:
        """Принимает закупку «в работу» из результатов поиска (US-5.4)."""
        _, profile = await _active_context(user)
        assert profile is not None
        notes = body.notes if body is not None else None
        item = await _repo().accept_into_work(
            procurement_id, profile.id, source="search", notes=notes
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        await _broadcast(state)
        return WorkItemOut.model_validate(item)

    @router.post(
        "/api/procurements/work/by-url",
        response_model=WorkItemOut,
        dependencies=[Depends(require_base)],
    )
    async def accept_into_work_by_url(
        body: AcceptWorkByUrlIn,
        user: User | None = Depends(require_base),
    ) -> WorkItemOut:
        """Принимает закупку «в работу» по URL на ЭТП (не из результатов поиска).

        Если закупка с таким URL есть в ``procurements`` — привязывается к ней;
        иначе создаётся запись-снимок (``procurement_id=NULL``), которая останется
        в «в работе», даже когда соответствующий результат поиска появится и будет
        удалён (FK SET NULL + снимок).
        """
        _, profile = await _active_context(user)
        assert profile is not None
        item = await _repo().accept_into_work_by_url(body.url, profile.id, notes=body.notes)
        await _broadcast(state)
        return WorkItemOut.model_validate(item)

    @router.delete(
        "/api/procurements/work/{work_item_id}",
        status_code=204,
        dependencies=[Depends(require_base)],
    )
    async def remove_work_item(
        work_item_id: int, user: User | None = Depends(require_base)
    ) -> None:
        """Удаляет запись «в работе» по её id (в т.ч. запись-снимок по URL)."""
        _, profile = await _active_context(user)
        assert profile is not None
        removed = await _repo().remove_work_item(profile.id, work_item_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        await _broadcast(state)

    @router.delete(
        "/api/procurements/{procurement_id}/work",
        status_code=204,
        dependencies=[Depends(require_base)],
    )
    async def remove_from_work(
        procurement_id: int, user: User | None = Depends(require_base)
    ) -> None:
        """Снимает закупку с «в работе» (удаляется только запись, не закупка)."""
        _, profile = await _active_context(user)
        assert profile is not None
        removed = await _repo().remove_from_work(profile.id, procurement_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Закупка не в работе")
        await _broadcast(state)

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
        return _procurement_detail_out(row, include_costs=_is_analyst(user))

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
        # resolve_tz_content_cached использует ту же логику поиска/извлечения,
        # что и конвейер анализа стоп-условий (scoring_common.tz), и кэширует
        # результат — при повторном открытии карточки файл заново не скачивается.
        async with _tz_extract_semaphore:
            ref, text = await asyncio.to_thread(resolve_tz_content_cached, record, 30.0)
        if ref is None or text is None:
            return {
                "found": False,
                "file_name": ref.name if ref is not None else None,
                "from_archive": "#" in ref.url if ref is not None else False,
                "text": None,
            }
        return {
            "found": True,
            "file_name": ref.name,
            "from_archive": "#" in ref.url,
            "text": text,
        }

    async def _resolve_requirements(row: Any) -> dict[str, Any]:
        """Детерминированное извлечение требований + сохранение в БД (per-procurement).

        Тяжёлые блокирующие операции (скачивание/конвертация всех документов карточки)
        выполняются в потоке под семафором ``_req_extract_semaphore``. Возвращает
        структуру (``{}`` — требования не найдены).
        """
        record = {"files_json": row.files_json or []}
        async with _req_extract_semaphore:
            structure = await asyncio.to_thread(extract_requirements, record, 30.0)
        await _repo().save_requirements(row.id, structure)
        return structure

    @router.get(
        "/api/procurements/{procurement_id}/requirements",
        response_model=RequirementsOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def get_procurement_requirements(
        procurement_id: int, user: User | None = Depends(require_user_or_internal)
    ) -> RequirementsOut:
        """Структура «Требования к участнику» для просмотра в карточке.

        Читает новое поле ``procurements.requirements_json``; если оно не заполнено
        (NULL) — выполняет детерминированное извлечение требований по всем документам
        карточки (``scoring_common.requirements``) и сохраняет результат в БД.
        """
        _, profile = await _active_context(user)
        row = await _repo().get_by_id(
            procurement_id, profile_id=profile.id if profile is not None else None
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        structure = row.requirements_json
        if structure is None:
            structure = await _resolve_requirements(row)
        return RequirementsOut(found=bool(structure), requirements=structure or {})

    @router.post(
        "/api/procurements/{procurement_id}/requirements",
        response_model=RequirementsOut,
        dependencies=[Depends(require_internal)],
    )
    async def set_procurement_requirements(
        procurement_id: int, body: RequirementsIn
    ) -> RequirementsOut:
        """Сохранить структуру требований к участнику (analysis-воркер).

        Внутренний эндпоинт: воркер-анализ возвращает заполненные ``data`` после
        LLM-обработки. Пишет в ``procurements.requirements_json`` (per-procurement).
        """
        if await _repo().save_requirements(procurement_id, body.structure) is False:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return RequirementsOut(found=bool(body.structure), requirements=body.structure)

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
        # Батч-метрики закупки (журнал «Метрики»): итерация цикла планировщика,
        # в которую закупка поставлена в очередь, и площадка. Копируются на
        # записываемую оценку напрямую из закупки (set_score уже читает её).
        batch_iteration = existing.scoring_iteration
        batch_platform = existing.platform_id
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
        # need_region: профиль явно запросил регион (target_regions/расстояние) —
        # если API-детали региона не дают, регион добирается из DOM (по требованию).
        target_profile = await _repo().get_profile_by_id(body.profile_id)
        await _fetch_details_for_score(
            state, existing, need_region=_region_explicitly_requested(target_profile)
        )
        # Регион мог стать известен только ПОСЛЕ досборки деталей (BR-08): повторная
        # клиентская фильтрация по региону (как ключевые слова R9). Если регион вне
        # целевых профиля — результат НЕ записывается, а оценка профиля удаляется
        # (профиль «не отобрал» закупку; recovery не будет ставить задание повторно).
        details_row = await _repo().get_by_id(procurement_id)
        region_value = _procurement_region(details_row)
        if (
            target_profile is not None
            and _region_string_filter_enabled(target_profile)
            and region_value
            and not region_match({"region": region_value}, target_profile.target_regions)
        ):
            await _repo().remove_evaluation(procurement_id, body.profile_id)
            logger.info(
                "Закупка %s: регион «%s» вне целевых профиля %s — результат скоринга не записан",
                procurement_id,
                region_value,
                body.profile_id,
            )
            await _broadcast(state)
            row = await _repo().get_by_id(procurement_id, profile_id=body.profile_id)
            if row is None:  # pragma: no cover - проверено выше
                raise HTTPException(status_code=404, detail="Закупка не найдена")
            return _procurement_detail_out(row, include_costs=True)
        # Стоимость обработки закупки: скоринг (body.score_costs) и анализ
        # (rag_report['cost']). Аналитическую стоимость вынимаем из rag_report ДО
        # сохранения, чтобы внутренняя метрика (USD) не персистилась/не отдавалась
        # в общем отчёте — стоимость доступна только роли analyst (см. converters).
        costs: dict[str, Any] = {}
        if body.score_costs is not None:
            costs["scoring"] = body.score_costs
        if body.rag_report and body.rag_report.get("cost"):
            costs["analysis"] = body.rag_report.pop("cost")
        if body.rag_report is not None:
            # Анализ стоп-условий: сохраняем отчёт профилю (score_method не меняем).
            await _repo().update_rag_report(
                procurement_id,
                body.profile_id,
                body.rag_report,
                costs=costs,
                iteration=batch_iteration,
                platform=batch_platform,
            )
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
                costs=costs,
                iteration=batch_iteration,
                platform=batch_platform,
            )
            # BR-07 (дедупликация по содержанию компетенций): результат, посчитанный
            # для представителя группы идентичного содержания компетенций,
            # распространяется на всех профилей, отобравших эту закупку с тем же
            # comp_hash (подписка). Один LLM-вызов на группу, результат — у всех.
            try:
                rep = await _repo().get_score(procurement_id, body.profile_id)
                if rep is not None and rep.comp_hash:
                    # Группа дедупликации (BR-07) может содержать профили с разными
                    # target_regions: регион уже известен (досборка деталей выше) —
                    # оценки профилей, чей регион вне целевых, удаляем, чтобы
                    # распространение результата их не затронуло.
                    if region_value:
                        for member in await _repo().list_group_evaluations(
                            procurement_id, rep.comp_hash
                        ):
                            if member.profile_id is None or member.profile_id == body.profile_id:
                                continue
                            member_profile = await _repo().get_profile_by_id(member.profile_id)
                            if member_profile is None:
                                continue
                            if _region_string_filter_enabled(member_profile) and not region_match(
                                {"region": region_value}, member_profile.target_regions
                            ):
                                await _repo().remove_evaluation(procurement_id, member.profile_id)
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
                        costs=costs,
                        iteration=batch_iteration,
                        platform=batch_platform,
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
        return _procurement_detail_out(row, include_costs=True)

    @router.post(
        "/api/procurements/analyze",
        include_in_schema=False,
        dependencies=[Depends(require_base)],
    )
    async def analyze_procurements(
        body: ProcurementIdsIn, user: User | None = Depends(require_base)
    ) -> dict[str, Any]:
        """Обработать выбранные закупки: авто-Fit (если нет) + анализ документов.

        Внутренние стадии скрыты от заказчика: для каждой закупки ставится
        задание fit (если per-profile fit ещё не посчитан) и затем analysis —
        заполнение ``data`` требований к участнику + персональные вопросы профиля.
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
