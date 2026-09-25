"""Эндпоинты профилей фильтрации (tenant-скоуп BR-07; пути /api/clients — для совместимости)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from zakupki_parser.api.app.condition_recheck import recheck_status, start_condition_recheck
from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.profile_source import (
    ProfileFromUrlError,
    ProfileFromUrlNotConfigured,
    generate_profile_from_url,
)
from zakupki_parser.api.app.schemas import (
    LicenseIn,
    ProfileExportOut,
    ProfileFromUrlIn,
    ProfileFromUrlOut,
    ProfileGeoIn,
    ProfileGeoOut,
    ProfileImportIn,
    ProfileIn,
    ProfileListOut,
    ProfileOut,
    ProfileSaveOut,
    UnmatchedLicenseOut,
)
from zakupki_parser.api.app.state import _broadcast, _sync_profile_results
from zakupki_parser.storage.db import User
from zakupki_parser.storage.profile_json import (
    parse_profile_json,
    resolve_profile_fact_refs,
    serialize_profile_json,
)

logger = logging.getLogger(__name__)


_TRANSLIT: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def _transliterate(text: str) -> str:
    """Переводит кириллицу в латиницу; прочие не-ASCII символы отбрасывает."""
    out: list[str] = []
    for ch in text:
        repl = _TRANSLIT.get(ch.casefold())
        if repl is not None:
            out.append(repl.upper() if ch.isupper() else repl)
        elif ch.isascii():
            out.append(ch)
    return "".join(out)


def _safe_filename(name: str) -> str:
    """Имя файла из имени профиля: латиница, недопустимые символы/пробелы — в подчёркивания."""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", _transliterate(name)).strip("._")
    return cleaned or "profile"


def _analysis_inputs_key(profile: Any, license_type_ids: list[int]) -> tuple[Any, ...]:
    """Входы анализа, которые НЕЛЬЗЯ пересчитать без повторного анализа.

    Условия отчётных полей и ``requirement_blocking`` пересчитываются кодом
    (``condition_recheck``); регионы/расстояние (гео-проверка) и лицензии
    профиля (сводка по лицензиям) — нет. Совпал ключ до и после правки —
    отчёты, актуальные до неё, остаются актуальными после пересчёта.
    """
    return (
        tuple(sorted(profile.target_regions or [])),
        profile.max_region_distance_km,
        tuple(sorted(license_type_ids)),
    )


def _crawl_state_key(profile: Any, words: dict[str, list[str]]) -> tuple[Any, ...]:
    """Ключ crawl-значимого состояния профиля для change-detection (fast-start).

    Сравниваются только поля, влияющие на обход/фильтрацию площадок; правки
    остальных (имя, вопросы, лицензии, опыт, min_fit_threshold и т.п.) не должны
    запускать внеочередной полный обход.
    """
    return (
        profile.enabled,
        tuple(sorted(profile.okpd_codes or [])),
        profile.nmck_min,
        profile.nmck_max,
        tuple(sorted(profile.target_etp or [])),
        tuple(sorted(profile.target_laws or [])),
        tuple(sorted(profile.target_regions or [])),
        profile.max_region_distance_km,
        tuple(sorted(words.get("keywords") or [])),
        tuple(sorted(words.get("exclusion_words") or [])),
    )


def _export_timestamp() -> str:
    """Временная метка для имени файла экспорта (дата + время, без секунд в разделе)."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def build_clients_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    _active_context = ctx._active_context
    _profile_out = ctx._profile_out
    _validate_profile_entries = ctx._validate_profile_entries
    _effective_options = ctx._effective_options
    require_base = ctx.require_base
    require_user_or_internal = ctx.require_user_or_internal

    def _require_user(user: User | None) -> User:
        """Реальный пользователь (авторизация всегда включена)."""
        if user is None:
            raise HTTPException(status_code=401, detail="Требуется авторизация")
        return user

    async def _profile_geo_out(client_id: int) -> ProfileGeoOut:
        """Кэш центров регионов профиля: пустой, если ещё не геокодирован."""
        row = await _repo().get_profile_geo_cache(client_id)
        if row is None:
            return ProfileGeoOut(profile_id=client_id, regions=[], centers=[])
        return ProfileGeoOut(
            profile_id=client_id,
            regions=list(row.geo_regions or []),
            centers=list(row.geo_centers or []),
        )

    async def _license_type_ids(profile_id: int) -> list[int]:
        return [lic.license_type_id for lic in await _repo().list_licenses(profile_id)]

    async def _export_licenses(profile_id: int) -> list[dict[str, Any]]:
        """Лицензии профиля -> переносимая форма (``license_type_name`` вместо id)."""
        types_map = await ctx._license_types_map()
        out: list[dict[str, Any]] = []
        for lic in await _repo().list_licenses(profile_id):
            kind = types_map.get(lic.license_type_id)
            out.append(
                {
                    "license_type_id": lic.license_type_id,
                    "license_type_name": kind.name if kind else None,
                    "number": lic.number,
                    "authority": lic.authority,
                    "issue_date": lic.issue_date,
                    "expiry_date": lic.expiry_date,
                    "notes": lic.notes,
                }
            )
        return out

    async def _export_experience(profile_id: int) -> list[dict[str, Any]]:
        """Опыт профиля -> переносимая форма (``confirmation_type_code`` вместо id)."""
        types_map = await ctx._confirmation_types_map()
        out: list[dict[str, Any]] = []
        for exp in await _repo().list_experience(profile_id):
            kind = types_map.get(exp.confirmation_type_id)
            out.append(
                {
                    "confirmation_type_id": exp.confirmation_type_id,
                    "confirmation_type_code": kind.code if kind else None,
                    "title": exp.title,
                    "customer_name": exp.customer_name,
                    "contract_number": exp.contract_number,
                    "start_date": exp.start_date,
                    "end_date": exp.end_date,
                    "amount": exp.amount,
                    "import_independent": exp.import_independent,
                    "notes": exp.notes,
                }
            )
        return out

    async def _request_refresh_for(
        profile: Any, *, rebuild: bool = False, rescore: bool = False
    ) -> bool:
        """Запрашивает сбор данных для включённого профиля (fast-start).

        Тонкая обёртка над общей ``state._sync_profile_results`` (см. её
        докстринг за деталями throttle/индексного пути) — вызывается после
        создания/изменения/принудительного обновления профиля.
        """
        return await _sync_profile_results(
            state, _repo(), profile, rebuild=rebuild, rescore=rescore
        )

    def _collection_notice(
        profile: Any,
        *,
        refresh_requested: bool,
        verb: str = "Профиль сохранён",
        fully_index_covered: bool = False,
    ) -> str:
        """Уведомление пользователю: когда начнётся/уже завершился сбор данных.

        Вызывается сразу после сохранения (или принудительного обновления —
        ``refresh_client``, ``verb="Обновление запрошено"``); ``refresh_requested``
        — запрошен ли внеочередной обход этой правкой (см. change-detection в
        ``update_client``; принудительное обновление запрашивает его всегда,
        пока профиль включён). ``fully_index_covered`` — все коды профиля
        обслужены фоновой индексацией синхронно (``_request_refresh_for``) —
        живого обхода площадок для него не было и не будет вовсе.
        """
        if not profile.enabled:
            return (
                "Профиль отключён — сбор данных по нему не выполняется. "
                "Включите профиль и сохраните его, чтобы начать сбор."
            )
        if fully_index_covered:
            return (
                f"{verb}. Все коды ОКПД2 профиля входят в проиндексированный "
                "диапазон — результаты уже пересобраны из индекса, обход "
                "площадок не требуется."
            )
        if not refresh_requested:
            return (
                f"{verb}. Изменения не влияют на критерии сбора "
                "(ОКПД2/слова/НМЦК/регионы/площадки) — сбор данных продолжится "
                "по регулярному расписанию мониторинга."
            )
        scheduler = state.parser_scheduler
        if scheduler is None:
            if profile.id in state.pending_profile_refresh_ids:
                return (
                    f"{verb}. Парсер остановлен: внеочередной сбор по "
                    "профилю начнётся сразу после запуска мониторинга."
                )
            return (
                f"{verb}. Парсер не запущен: сбор данных по профилю "
                "начнётся после запуска мониторинга на панели devops."
            )
        status = scheduler.profile_refresh_status(profile.id)
        remaining = status.get("remaining_seconds")
        if remaining is not None and remaining > 0:
            total = int(remaining)
            approx = f"{total // 60} мин {total % 60} с" if total >= 60 else f"{total} с"
            return (
                f"{verb}. Внеочередной сбор данных по нему начнётся "
                f"не ранее чем через {approx} после завершения предыдущего "
                "внеочередного обхода профиля."
            )
        return (
            f"{verb}. Внеочередной сбор данных по нему начнётся сразу "
            "после завершения текущего прохода (если он идёт) — в ближайшее окно "
            "между проходами мониторинга."
        )

    async def _save_out(
        profile: Any,
        notice: str | None,
        keywords: dict[str, list[str]] | None = None,
    ) -> ProfileSaveOut:
        """Карточка сохранённого профиля + уведомление о начале сбора."""
        base = await _profile_out(profile, keywords=keywords)
        return ProfileSaveOut(**base.model_dump(), notice=notice)

    @router.get(
        "/api/clients/active",
        response_model=ProfileOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def active_client(
        request: Request,
        user: User | None = Depends(require_user_or_internal),
    ) -> ProfileOut:
        """Профиль для анализа: активный профиль пользователя или явный профиль конвейера.

        - реальный пользователь: его активный профиль (контекст фильтрации BR-07);
        - внутренний вызов конвейера (``X-Internal-Token``): профиль из заголовка
          ``X-Profile-ID`` (системный скоуп, без сервис-аккаунта).
        """
        if user is None:
            raw = request.headers.get("X-Profile-ID")
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail="Внутренний вызов: укажите профиль заголовком X-Profile-ID",
                )
            try:
                profile_id = int(raw)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="X-Profile-ID должен быть целым числом"
                ) from None
            profile = await _repo().get_profile_by_id(profile_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="Профиль не найден")
            return await _profile_out(profile, include_facts=True)
        _, profile = await _active_context(_require_user(user))
        assert profile is not None
        return await _profile_out(profile, include_facts=True)

    @router.get(
        "/api/clients",
        response_model=ProfileListOut,
        dependencies=[Depends(require_base)],
    )
    async def list_clients(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        user: User | None = Depends(require_base),
    ) -> ProfileListOut:
        eff_user = _require_user(user)
        rows, total = await _repo().list_profiles(user_id=eff_user.id, limit=limit, offset=offset)
        # Батч-чтение слов профилей (без N+1 по таблице keywords).
        keywords = await _repo().list_profiles_keywords([r.id for r in rows])
        return ProfileListOut(
            total=total, items=[await _profile_out(r, keywords.get(r.id)) for r in rows]
        )

    @router.get(
        "/api/clients/{client_id}",
        response_model=ProfileOut,
        dependencies=[Depends(require_base)],
    )
    async def get_client(client_id: int, user: User | None = Depends(require_base)) -> ProfileOut:
        eff_user = _require_user(user)
        row = await _repo().get_profile(eff_user.id, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        return await _profile_out(row)

    @router.get(
        "/api/clients/{client_id}/geo",
        response_model=ProfileGeoOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def get_client_geo(
        client_id: int, user: User | None = Depends(require_user_or_internal)
    ) -> ProfileGeoOut:
        """Кэш координат центров целевых регионов профиля (этап анализа).

        Возвращает сохранённый набор регионов и координаты. Повторное геокодирование
        профиля нужно только при изменении ``target_regions`` (сравнивается с
        ``regions``). Доступ — внутренний (analysis_service) или владелец профиля.
        """
        if user is None:
            profile = await _repo().get_profile_by_id(client_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="Профиль не найден")
            return await _profile_geo_out(client_id)
        eff_user = _require_user(user)
        row = await _repo().get_profile(eff_user.id, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        return await _profile_geo_out(client_id)

    @router.put(
        "/api/clients/{client_id}/geo",
        response_model=ProfileGeoOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def put_client_geo(
        client_id: int,
        body: ProfileGeoIn,
        user: User | None = Depends(require_user_or_internal),
    ) -> ProfileGeoOut:
        """Сохраняет кэш координат центров целевых регионов профиля (analysis_service)."""
        await _repo().upsert_profile_geo_cache(client_id, body.regions, body.centers)
        return await _profile_geo_out(client_id)

    @router.get(
        "/api/clients/{client_id}/export",
        response_model=ProfileExportOut,
        dependencies=[Depends(require_base)],
    )
    async def export_client(
        client_id: int,
        user: User | None = Depends(require_base),
    ) -> ProfileExportOut:
        """Экспорт профиля единым JSON-файлом (компетенции — подобъект внутри).

        Имя файла — из имени профиля и даты/времени. Файл самодостаточен: его
        можно повторно загрузить через ``/api/clients/import``.
        """
        eff_user = _require_user(user)
        row = await _repo().get_profile(eff_user.id, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        profile = await _profile_out(row)
        data = profile.model_dump()
        # Факты BR-03 (лицензии/опыт) — часть структуры профиля и переносятся в файл
        # переносимыми ссылками (наименование/код), а не числовыми id справочников.
        data["licenses"] = await _export_licenses(row.id)
        data["experience"] = await _export_experience(row.id)
        # Сопоставление колонок шаблона отчёта заказчика (FR-12.4) в карточку
        # профиля не входит, но часть профиля — переносится файлом.
        data["report_field_mapping"] = dict(row.report_field_mapping or {})
        safe = _safe_filename(data["name"] or "profile")
        profile_filename = f"{safe}_{_export_timestamp()}.json"
        return ProfileExportOut(
            profile_filename=profile_filename,
            profile_content=serialize_profile_json(data),
        )

    @router.post(
        "/api/clients",
        response_model=ProfileSaveOut,
        dependencies=[Depends(require_base)],
    )
    async def create_client(
        body: ProfileIn, user: User | None = Depends(require_base)
    ) -> ProfileSaveOut:
        eff_user = _require_user(user)
        await _validate_profile_entries(body)
        try:
            profile = await _repo().upsert_profile(
                body.model_dump(exclude_none=True),
                eff_user.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        covered = await _request_refresh_for(profile)
        notice = _collection_notice(
            profile, refresh_requested=bool(profile.enabled), fully_index_covered=covered
        )
        return await _save_out(profile, notice)

    @router.put(
        "/api/clients/{client_id}",
        response_model=ProfileSaveOut,
        dependencies=[Depends(require_base)],
    )
    async def update_client(
        client_id: int, body: ProfileIn, user: User | None = Depends(require_base)
    ) -> ProfileSaveOut:
        eff_user = _require_user(user)
        existing = await _repo().get_profile(eff_user.id, client_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        # PUT — полная замена: обновляем существующий профиль по id (в т.ч. при
        # переименовании — раньше upsert по name создавал новый профиль), null
        # сохраняется как null (exclude_unset, а не exclude_none).
        # Change-detection: внеочередной обход запрашиваем только при фактическом
        # изменении crawl-полей (иначе rename/no-op сохранения гоняли бы полный обход).
        old_words = await _repo().get_profile_keywords(existing.id)
        old_key = _crawl_state_key(existing, old_words)
        old_updated_at = existing.updated_at
        old_analysis_key = _analysis_inputs_key(existing, await _license_type_ids(existing.id))
        await _validate_profile_entries(body)
        try:
            updated = await _repo().upsert_profile(
                body.model_dump(exclude_unset=True),
                eff_user.id,
                profile_id=client_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        new_words = await _repo().get_profile_keywords(updated.id)
        crawl_changed = _crawl_state_key(updated, new_words) != old_key
        # Условия полей/блокировки пересчитываются по готовым отчётам без LLM.
        start_condition_recheck(
            state,
            updated,
            snapshot_from=old_updated_at,
            keep_fresh=_analysis_inputs_key(updated, await _license_type_ids(updated.id))
            == old_analysis_key,
        )
        # Изменились компетенции (хэш канонического содержания) — результаты сбора
        # нужно перестроить и скор пересчитать по новой области захвата.
        from zakupki_parser.storage.competencies import competencies_hash

        comp_changed = competencies_hash(existing.competencies) != competencies_hash(
            updated.competencies
        )
        rebuild = crawl_changed or comp_changed
        covered = False
        if rebuild:
            covered = await _request_refresh_for(updated, rebuild=True, rescore=comp_changed)
        notice = _collection_notice(
            updated,
            refresh_requested=rebuild and bool(updated.enabled),
            fully_index_covered=covered,
        )
        return await _save_out(updated, notice, keywords=new_words)

    @router.get(
        "/api/clients/{client_id}/recheck",
        dependencies=[Depends(require_base)],
    )
    async def client_recheck_status(
        client_id: int, user: User | None = Depends(require_base)
    ) -> dict[str, Any]:
        """Ход пересчёта условий отчётных полей по отчётам профиля (без LLM).

        ``running`` — идёт; ``done``/``total`` — пересчитано отчётов из скольких;
        ``stale`` — отчётов, которым нужен повторный анализ (новое/изменённое
        поле или LLM-условие). Пересчёта не было — ``running=false, total=0``.
        """
        eff_user = _require_user(user)
        if await _repo().get_profile(eff_user.id, client_id) is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        status = recheck_status(state, client_id)
        if status is None:
            return {"profile_id": client_id, "running": False, "total": 0, "done": 0, "stale": 0}
        return status.as_dict()

    @router.post(
        "/api/clients/{client_id}/refresh",
        response_model=ProfileSaveOut,
        dependencies=[Depends(require_base)],
    )
    async def refresh_client(
        client_id: int, user: User | None = Depends(require_base)
    ) -> ProfileSaveOut:
        """Принудительное обновление («Обновить сейчас») — БЕЗ изменения самого
        профиля, тот же fast-start путь, что и сохранение профиля с изменением
        критериев сбора (``_request_refresh_for``, ``rebuild=True``). Для
        профиля, ЦЕЛИКОМ покрытого фоновой индексацией, throttle не действует
        вовсе — повторное нажатие снова синхронно пересобирает результаты из
        индекса (дёшево, живой обход площадок при этом не идёт). Для
        остального (частичное покрытие/живой обход) — throttle тот же
        ``profile_refresh_debounce_seconds``, что и у обычных правок (привязан
        к id профиля, а не к причине запроса, см. ``Scheduler.
        request_profile_refresh``): повторное нажатие раньше, чем истёк
        throttle с предыдущего обхода, не запускает новый обход, только
        удлиняет ожидание в уведомлении.
        """
        eff_user = _require_user(user)
        profile = await _repo().get_profile(eff_user.id, client_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        covered = await _request_refresh_for(profile, rebuild=True)
        notice = _collection_notice(
            profile,
            refresh_requested=bool(profile.enabled),
            verb="Обновление запрошено",
            fully_index_covered=covered,
        )
        return await _save_out(profile, notice)

    @router.post(
        "/api/clients/{client_id}/activate",
        response_model=ProfileOut,
        dependencies=[Depends(require_base)],
    )
    async def activate_client(
        client_id: int, user: User | None = Depends(require_base)
    ) -> ProfileOut:
        """Делает профиль активным (per-user состояние; остальные деактивируются)."""
        eff_user = _require_user(user)
        try:
            profile = await _repo().set_active_profile(eff_user.id, client_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await _profile_out(profile)

    @router.delete(
        "/api/clients/{client_id}",
        status_code=204,
        dependencies=[Depends(require_base)],
    )
    async def delete_client(client_id: int, user: User | None = Depends(require_base)) -> None:
        """Удаляет профиль (нельзя удалить активный или последний)."""
        eff_user = _require_user(user)
        try:
            await _repo().delete_profile(eff_user.id, client_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await _broadcast(state)

    @router.post(
        "/api/clients/import",
        response_model=ProfileSaveOut,
        dependencies=[Depends(require_base)],
    )
    async def import_client(
        payload: ProfileImportIn, user: User | None = Depends(require_base)
    ) -> ProfileSaveOut:
        """Загружает/обновляет профиль из загруженного файла.

        Формат — единый JSON-файл (компетенции — подобъект внутри схемы
        ``Profile``, BR-07).
        """
        eff_user = _require_user(user)
        # Некорректный файл (не JSON, не формат zakupki-profile, не-объектные
        # компетенции) — понятная ошибка 422, а не 500.
        try:
            seed = parse_profile_json(payload.content)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Файл не распознан как профиль ({exc})",
            ) from exc
        # Факты BR-03 переносятся ссылками (наименование/код): резолвим их в id
        # справочников целевой БД перед записью (иначе имя/код не лягут в FK).
        license_map = {t.name: t.id for t in await _repo().list_license_types()}
        confirmation_map = {c.code: c.id for c in await _repo().list_confirmation_types()}
        try:
            seed = resolve_profile_fact_refs(seed, license_map, confirmation_map)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        name = seed.get("name") or "default"
        existing = await _repo().get_profile_by_name(eff_user.id, name)
        old_analysis_key = (
            _analysis_inputs_key(existing, await _license_type_ids(existing.id))
            if existing is not None
            else None
        )
        try:
            profile = await _repo().upsert_profile(
                {**seed, "name": name},
                eff_user.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("Профиль %s (id=%s) загружен из файла (web)", name, profile.id)
        await _broadcast(state)
        if existing is not None:
            start_condition_recheck(
                state,
                profile,
                snapshot_from=existing.updated_at,
                keep_fresh=_analysis_inputs_key(profile, await _license_type_ids(profile.id))
                == old_analysis_key,
            )
        # Импорт обновляет существующий профиль: перестраиваем результаты сбора
        # (и пересчитываем скор, если изменились компетенции). Новый профиль —
        # по нему начинает идти обход (результатов ещё нет), а покрытая
        # индексом часть кодов даёт результаты сразу же (_request_refresh_for).
        from zakupki_parser.storage.competencies import competencies_hash

        comp_changed = existing is not None and competencies_hash(
            existing.competencies
        ) != competencies_hash(profile.competencies)
        covered = await _request_refresh_for(
            profile,
            rebuild=existing is not None,
            rescore=comp_changed,
        )
        notice = _collection_notice(
            profile, refresh_requested=bool(profile.enabled), fully_index_covered=covered
        )
        return await _save_out(profile, notice)

    @router.post(
        "/api/clients/profile/from-url",
        response_model=ProfileFromUrlOut,
        dependencies=[Depends(require_base)],
    )
    async def profile_from_url(
        payload: ProfileFromUrlIn, user: User | None = Depends(require_base)
    ) -> ProfileFromUrlOut:
        """Формирует компетенции и лицензии профиля по сайту поставщика (LLM).

        Скачивает URL (SSRF-защищённо), извлекает текст, просит LLM собрать
        компетенции по канонической схеме ``Profile`` и лицензии, сопоставленные
        со справочником ``license_types`` (несуществующие типы отфильтрованы) —
        результат подставляется в редактор (вкладки «Компетенции» и «Лицензии»)
        для проверки пользователем, профиль не меняется автоматически.

        Платная опция аккаунта (``profile_from_url``, options.py) — включена
        по умолчанию при саморегистрации (в отличие от ``scoring``, которую
        пользователь включает сам).
        """
        eff_user = _require_user(user)
        eff = await _effective_options(eff_user)
        if not eff.has_option("profile_from_url"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Заполнение профиля по сайту недоступно в вашем аккаунте: это "
                    "платная опция (LLM). Включите её в личном кабинете."
                ),
            )
        license_types = [(t.id, t.name) for t in await _repo().list_license_types()]
        try:
            result = await generate_profile_from_url(payload.url, license_types)
        except ProfileFromUrlNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProfileFromUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ProfileFromUrlOut(
            competencies=result.competencies,
            licenses=[LicenseIn(**lic) for lic in result.licenses],
            unmatched_licenses=[UnmatchedLicenseOut(**lic) for lic in result.unmatched_licenses],
        )

    return router
