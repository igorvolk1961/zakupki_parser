"""Эндпоинты лицензий и подтверждённого опыта профиля (справочники + CRUD, BR-03)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    ConfirmationTypeOut,
    ExperienceIn,
    ExperienceListOut,
    ExperienceOut,
    LicenseIn,
    LicenseListOut,
    LicenseOut,
    LicenseTypeOut,
)
from zakupki_parser.storage.db import User


def build_facts_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _repo = ctx._repo
    _owned_profile = ctx._owned_profile
    _license_types_map = ctx._license_types_map
    _confirmation_types_map = ctx._confirmation_types_map
    _license_out = ctx._license_out
    _experience_out = ctx._experience_out
    require_user = ctx.require_user

    @router.get(
        "/api/license-types",
        response_model=list[LicenseTypeOut],
        dependencies=[Depends(require_user)],
    )
    async def list_license_types() -> list[LicenseTypeOut]:
        """Справочник типов лицензий (для выбора в редакторе профиля)."""
        rows = await _repo().list_license_types()
        return [LicenseTypeOut.model_validate(r) for r in rows]

    @router.get(
        "/api/confirmation-types",
        response_model=list[ConfirmationTypeOut],
        dependencies=[Depends(require_user)],
    )
    async def list_confirmation_types() -> list[ConfirmationTypeOut]:
        """Справочник типов подтверждения опыта (сид BR-03)."""
        rows = await _repo().list_confirmation_types()
        return [ConfirmationTypeOut.model_validate(r) for r in rows]

    @router.get(
        "/api/clients/{client_id}/licenses",
        response_model=LicenseListOut,
        dependencies=[Depends(require_user)],
    )
    async def list_licenses(
        client_id: int, user: User | None = Depends(require_user)
    ) -> LicenseListOut:
        await _owned_profile(user, client_id)
        rows = await _repo().list_licenses(client_id)
        types_map = await _license_types_map()
        return LicenseListOut(total=len(rows), items=[_license_out(r, types_map) for r in rows])

    @router.post(
        "/api/clients/{client_id}/licenses",
        response_model=LicenseOut,
        dependencies=[Depends(require_user)],
    )
    async def create_license(
        client_id: int, body: LicenseIn, user: User | None = Depends(require_user)
    ) -> LicenseOut:
        await _owned_profile(user, client_id)
        types_map = await _license_types_map()
        if body.license_type_id not in types_map:
            raise HTTPException(status_code=422, detail="Неизвестный тип лицензии")
        row = await _repo().create_license(client_id, body.model_dump())
        return _license_out(row, types_map)

    @router.put(
        "/api/clients/{client_id}/licenses/{license_id}",
        response_model=LicenseOut,
        dependencies=[Depends(require_user)],
    )
    async def update_license(
        client_id: int,
        license_id: int,
        body: LicenseIn,
        user: User | None = Depends(require_user),
    ) -> LicenseOut:
        await _owned_profile(user, client_id)
        # 404 раньше валидации ссылки на справочник: статусы не зависят от тела.
        if await _repo().get_license(client_id, license_id) is None:
            raise HTTPException(status_code=404, detail="Лицензия не найдена")
        types_map = await _license_types_map()
        if body.license_type_id not in types_map:
            raise HTTPException(status_code=422, detail="Неизвестный тип лицензии")
        row = await _repo().update_license(client_id, license_id, body.model_dump())
        return _license_out(row, types_map)

    @router.delete(
        "/api/clients/{client_id}/licenses/{license_id}",
        status_code=204,
        dependencies=[Depends(require_user)],
    )
    async def delete_license(
        client_id: int, license_id: int, user: User | None = Depends(require_user)
    ) -> None:
        await _owned_profile(user, client_id)
        if not await _repo().delete_license(client_id, license_id):
            raise HTTPException(status_code=404, detail="Лицензия не найдена")

    @router.get(
        "/api/clients/{client_id}/experience",
        response_model=ExperienceListOut,
        dependencies=[Depends(require_user)],
    )
    async def list_experience(
        client_id: int, user: User | None = Depends(require_user)
    ) -> ExperienceListOut:
        await _owned_profile(user, client_id)
        rows = await _repo().list_experience(client_id)
        types_map = await _confirmation_types_map()
        return ExperienceListOut(
            total=len(rows), items=[_experience_out(r, types_map) for r in rows]
        )

    @router.post(
        "/api/clients/{client_id}/experience",
        response_model=ExperienceOut,
        dependencies=[Depends(require_user)],
    )
    async def create_experience(
        client_id: int, body: ExperienceIn, user: User | None = Depends(require_user)
    ) -> ExperienceOut:
        await _owned_profile(user, client_id)
        types_map = await _confirmation_types_map()
        if body.confirmation_type_id not in types_map:
            raise HTTPException(status_code=422, detail="Неизвестный тип подтверждения")
        row = await _repo().create_experience(client_id, body.model_dump())
        return _experience_out(row, types_map)

    @router.put(
        "/api/clients/{client_id}/experience/{experience_id}",
        response_model=ExperienceOut,
        dependencies=[Depends(require_user)],
    )
    async def update_experience(
        client_id: int,
        experience_id: int,
        body: ExperienceIn,
        user: User | None = Depends(require_user),
    ) -> ExperienceOut:
        await _owned_profile(user, client_id)
        # 404 раньше валидации ссылки на справочник: статусы не зависят от тела.
        if await _repo().get_experience(client_id, experience_id) is None:
            raise HTTPException(status_code=404, detail="Запись опыта не найдена")
        types_map = await _confirmation_types_map()
        if body.confirmation_type_id not in types_map:
            raise HTTPException(status_code=422, detail="Неизвестный тип подтверждения")
        row = await _repo().update_experience(client_id, experience_id, body.model_dump())
        return _experience_out(row, types_map)

    @router.delete(
        "/api/clients/{client_id}/experience/{experience_id}",
        status_code=204,
        dependencies=[Depends(require_user)],
    )
    async def delete_experience(
        client_id: int, experience_id: int, user: User | None = Depends(require_user)
    ) -> None:
        await _owned_profile(user, client_id)
        if not await _repo().delete_experience(client_id, experience_id):
            raise HTTPException(status_code=404, detail="Запись опыта не найдена")

    return router
