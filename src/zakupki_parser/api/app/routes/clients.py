"""Эндпоинты профилей фильтрации (tenant-скоуп BR-07; пути /api/clients — для совместимости)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    ProfileExportOut,
    ProfileImportIn,
    ProfileIn,
    ProfileListOut,
    ProfileOut,
    ProfileSaveOut,
)
from zakupki_parser.api.app.state import _broadcast, _request_profile_refresh
from zakupki_parser.storage.db import User
from zakupki_parser.storage.profile_json import parse_profile_json, serialize_profile_json

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

    def _request_refresh_for(profile: Any) -> None:
        """Запрашивает внеочередной обход включённого профиля (fast-start).

        Новый/изменённый включённый профиль планировщик обработает сразу после
        текущего прохода, не дожидаясь следующего регулярного цикла. Отключённые
        профили не сигналим: при включении сигнал придёт со следующим сохранением.
        """
        if profile is not None and profile.enabled:
            _request_profile_refresh(state, profile.id)

    def _collection_notice(profile: Any, *, refresh_requested: bool) -> str:
        """Уведомление пользователю: когда начнётся сбор данных по профилю.

        Вызывается сразу после сохранения; ``refresh_requested`` — запрошен ли
        внеочередной обход этой правкой (см. change-detection в ``update_client``).
        """
        if not profile.enabled:
            return (
                "Профиль отключён — сбор данных по нему не выполняется. "
                "Включите профиль и сохраните его, чтобы начать сбор."
            )
        if not refresh_requested:
            return (
                "Профиль сохранён. Изменения не влияют на критерии сбора "
                "(ОКПД2/слова/НМЦК/регионы/площадки) — сбор данных продолжится "
                "по регулярному расписанию мониторинга."
            )
        scheduler = state.parser_scheduler
        if scheduler is None:
            if profile.id in state.pending_profile_refresh_ids:
                return (
                    "Профиль сохранён. Парсер остановлен: внеочередной сбор по "
                    "профилю начнётся сразу после запуска мониторинга."
                )
            return (
                "Профиль сохранён. Парсер не запущен: сбор данных по профилю "
                "начнётся после запуска мониторинга на панели devops."
            )
        status = scheduler.profile_refresh_status(profile.id)
        if status.get("handled_this_cycle"):
            return (
                "Профиль сохранён. Внеочередной сбор по нему уже выполнялся в "
                "текущем цикле: эта правка будет учтена следующим проходом "
                "мониторинга."
            )
        remaining = status.get("remaining_seconds")
        if remaining is not None and remaining > 0:
            total = int(remaining)
            approx = f"{total // 60} мин {total % 60} с" if total >= 60 else f"{total} с"
            return (
                "Профиль сохранён. Внеочередной сбор данных по нему начнётся "
                f"после завершения текущего прохода — не ранее чем через {approx} "
                "после последнего сохранения."
            )
        return (
            "Профиль сохранён. Внеочередной сбор данных по нему начнётся сразу "
            "после завершения текущего прохода мониторинга."
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
        eff = await _effective_options(eff_user)
        try:
            profile = await _repo().upsert_profile(
                body.model_dump(exclude_none=True),
                eff_user.id,
                require_competencies=eff.account_provides_competency_scoring(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _request_refresh_for(profile)
        notice = _collection_notice(profile, refresh_requested=bool(profile.enabled))
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
        await _validate_profile_entries(body)
        eff = await _effective_options(eff_user)
        try:
            updated = await _repo().upsert_profile(
                body.model_dump(exclude_unset=True),
                eff_user.id,
                profile_id=client_id,
                require_competencies=eff.account_provides_competency_scoring(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        new_words = await _repo().get_profile_keywords(updated.id)
        crawl_changed = _crawl_state_key(updated, new_words) != old_key
        if crawl_changed:
            _request_refresh_for(updated)
        notice = _collection_notice(
            updated, refresh_requested=crawl_changed and bool(updated.enabled)
        )
        return await _save_out(updated, notice, keywords=new_words)

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
        "/api/clients/seed",
        response_model=ProfileSaveOut,
        dependencies=[Depends(require_base)],
    )
    async def seed_client(user: User | None = Depends(require_base)) -> ProfileSaveOut:
        """Загружает/обновляет профиль из файла-сида профиля
        (как CLI ``zp seed-profile``).

        Имя профиля берётся из файла (секция ``**name**``); при отсутствии —
        ``default``. Активный профиль пользователя становится засиженным.
        """
        from zakupki_parser.storage.keywords_parser import parse_keywords_file

        eff_user = _require_user(user)
        seed = parse_keywords_file()
        name = seed.get("name") or "default"
        eff = await _effective_options(eff_user)
        try:
            profile = await _repo().upsert_profile(
                {**seed, "name": name},
                eff_user.id,
                require_competencies=eff.account_provides_competency_scoring(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info(
            "Профиль %s (id=%s) засижен из web-интерфейса (файл-сид профиля)",
            name,
            profile.id,
        )
        await _broadcast(state)
        _request_refresh_for(profile)
        notice = _collection_notice(profile, refresh_requested=bool(profile.enabled))
        return await _save_out(profile, notice)

    @router.post(
        "/api/clients/import",
        response_model=ProfileSaveOut,
        dependencies=[Depends(require_base)],
    )
    async def import_client(
        payload: ProfileImportIn, user: User | None = Depends(require_base)
    ) -> ProfileSaveOut:
        """Загружает/обновляет профиль из загруженного файла.

        Основной формат — единый JSON-файл (компетенции — подобъект внутри схемы
        ``Profile``, BR-07). Legacy-markdown-файлы сида не поддерживаются: легаси
        удалено, компетенции всегда канонический JSON.
        """
        eff_user = _require_user(user)
        seed = parse_profile_json(payload.content)
        name = seed.get("name") or "default"
        eff = await _effective_options(eff_user)
        try:
            profile = await _repo().upsert_profile(
                {**seed, "name": name},
                eff_user.id,
                require_competencies=eff.account_provides_competency_scoring(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("Профиль %s (id=%s) загружен из файла (web)", name, profile.id)
        await _broadcast(state)
        _request_refresh_for(profile)
        notice = _collection_notice(profile, refresh_requested=bool(profile.enabled))
        return await _save_out(profile, notice)

    return router
