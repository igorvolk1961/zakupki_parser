"""Эндпоинты профилей фильтрации (tenant-скоуп BR-07; пути /api/clients — для совместимости)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import ProfileIn, ProfileListOut, ProfileOut
from zakupki_parser.api.app.state import _broadcast
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)


def build_clients_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    _effective_user = ctx._effective_user
    _active_context = ctx._active_context
    _profile_out = ctx._profile_out
    _validate_profile_entries = ctx._validate_profile_entries
    require_user = ctx.require_user
    require_user_or_internal = ctx.require_user_or_internal

    @router.get(
        "/api/clients/active",
        response_model=ProfileOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def active_client(
        user: User | None = Depends(require_user_or_internal),
    ) -> ProfileOut:
        """Активный профиль эффективного пользователя (внутренний токен — сервис-аккаунт)."""
        _, profile = await _active_context(user)
        return await _profile_out(profile, include_facts=True)

    @router.get(
        "/api/clients",
        response_model=ProfileListOut,
        dependencies=[Depends(require_user)],
    )
    async def list_clients(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        user: User | None = Depends(require_user),
    ) -> ProfileListOut:
        eff_user = await _effective_user(user)
        rows, total = await _repo().list_profiles(user_id=eff_user.id, limit=limit, offset=offset)
        # Батч-чтение слов профилей (без N+1 по таблице keywords).
        keywords = await _repo().list_profiles_keywords([r.id for r in rows])
        return ProfileListOut(
            total=total, items=[await _profile_out(r, keywords.get(r.id)) for r in rows]
        )

    @router.get(
        "/api/clients/{client_id}",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def get_client(client_id: int, user: User | None = Depends(require_user)) -> ProfileOut:
        eff_user = await _effective_user(user)
        row = await _repo().get_profile(eff_user.id, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        return await _profile_out(row)

    @router.post(
        "/api/clients",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def create_client(
        body: ProfileIn, user: User | None = Depends(require_user)
    ) -> ProfileOut:
        eff_user = await _effective_user(user)
        await _validate_profile_entries(body)
        return await _profile_out(
            await _repo().upsert_profile(body.model_dump(exclude_none=True), eff_user.id)
        )

    @router.put(
        "/api/clients/{client_id}",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def update_client(
        client_id: int, body: ProfileIn, user: User | None = Depends(require_user)
    ) -> ProfileOut:
        eff_user = await _effective_user(user)
        existing = await _repo().get_profile(eff_user.id, client_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        # PUT — полная замена: обновляем существующий профиль по id (в т.ч. при
        # переименовании — раньше upsert по name создавал новый профиль), null
        # сохраняется как null (exclude_unset, а не exclude_none).
        await _validate_profile_entries(body)
        updated = await _repo().upsert_profile(
            body.model_dump(exclude_unset=True), eff_user.id, profile_id=client_id
        )
        return await _profile_out(updated)

    @router.post(
        "/api/clients/{client_id}/activate",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def activate_client(
        client_id: int, user: User | None = Depends(require_user)
    ) -> ProfileOut:
        """Делает профиль активным (per-user состояние; остальные деактивируются)."""
        eff_user = await _effective_user(user)
        try:
            profile = await _repo().set_active_profile(eff_user.id, client_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await _profile_out(profile)

    @router.delete(
        "/api/clients/{client_id}",
        status_code=204,
        dependencies=[Depends(require_user)],
    )
    async def delete_client(client_id: int, user: User | None = Depends(require_user)) -> None:
        """Удаляет профиль (нельзя удалить активный или последний)."""
        eff_user = await _effective_user(user)
        try:
            await _repo().delete_profile(eff_user.id, client_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await _broadcast(state)

    @router.post(
        "/api/clients/seed",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def seed_client(user: User | None = Depends(require_user)) -> ProfileOut:
        """Загружает/обновляет профиль из ``docs/references/profile.md``
        (как CLI ``zp seed-profile``).

        Имя профиля берётся из файла (секция ``**name**``); при отсутствии —
        ``default``. Активный профиль пользователя становится засиженным.
        """
        from zakupki_parser.storage.keywords_parser import parse_keywords_file

        eff_user = await _effective_user(user)
        seed = parse_keywords_file()
        name = seed.get("name") or "default"
        profile = await _repo().upsert_profile({**seed, "name": name}, eff_user.id)
        logger.info(
            "Профиль %s (id=%s) засижен из web-демо (файл docs/references/profile.md)",
            name,
            profile.id,
        )
        await _broadcast(state)
        return await _profile_out(profile)

    return router
