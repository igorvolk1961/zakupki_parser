"""Админ-эндпоинты управления пользователями (вкладка «Пользователи», роль admin).

Правила:
- создать пользователя можно с ролями {admin, analyst, devops} (роль «user» —
  только саморегистрация); создаётся default-профиль, как при регистрации;
- роли можно менять у пользователей с не-user ролями; роль «user» не выдаётся
  и не снимается; себе роли не меняем; нельзя снять admin у последнего admin;
- блокировка/разблокировка (status) — любому, кроме себя;
- удаление — любому, кроме себя и последнего admin (профили/оценки удаляются
  каскадом по FK).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    UserIn,
    UserOut,
    UserRolesIn,
    UsersListOut,
    UserStatusIn,
)
from zakupki_parser.auth import ROLE_ADMIN, ROLE_USER, hash_password
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)


def build_users_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _repo = ctx._repo
    require_admin = ctx.require_admin

    @router.get("/api/users", response_model=UsersListOut, dependencies=[Depends(require_admin)])
    async def list_users() -> UsersListOut:
        """Все пользователи сервиса (для админ-вкладки «Пользователи»)."""
        users = await _repo().list_users()
        return UsersListOut(total=len(users), items=[UserOut.model_validate(u) for u in users])

    @router.post(
        "/api/users",
        response_model=UserOut,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    async def create_user(body: UserIn, admin: User | None = Depends(require_admin)) -> UserOut:
        """Создаёт пользователя с ролями {admin, analyst, devops} (не user)."""
        if await _repo().get_user_by_username(body.username) is not None:
            raise HTTPException(status_code=409, detail="Пользователь с таким логином уже есть")
        password_hash = await asyncio.to_thread(hash_password, body.password)
        try:
            user = await _repo().create_user(
                body.username, password_hash, list(body.roles), email=body.email
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="Пользователь с таким логином уже есть"
            ) from exc
        # Активный профиль default — как при регистрации: без него список закупок
        # недоступен (нет контекста фильтрации) для ролей с базовыми вкладками.
        await _repo().ensure_default_profile(user.id)
        logger.info(
            "Админ %s создал пользователя %s (роли %s)",
            admin.username if admin else "?",
            user.username,
            ",".join(user.roles),
        )
        return UserOut.model_validate(user)

    @router.patch(
        "/api/users/{user_id}/roles",
        response_model=UserOut,
        dependencies=[Depends(require_admin)],
    )
    async def update_roles(
        user_id: int, body: UserRolesIn, admin: User | None = Depends(require_admin)
    ) -> UserOut:
        """Меняет роли пользователя (кроме простых и самого себя)."""
        target = await _repo().get_user(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if admin is not None and target.id == admin.id:
            raise HTTPException(status_code=403, detail="Нельзя менять роли себе")
        if set(target.roles) == {"user"}:
            raise HTTPException(status_code=409, detail="Роли простого пользователя менять нельзя")
        # Роль «user» не выдаётся и не снимается: если она уже есть у пользователя,
        # сохраняем её при смене остальных ролей.
        new_roles = sorted(set(body.roles) | ({ROLE_USER} if ROLE_USER in target.roles else set()))
        if ROLE_ADMIN in target.roles and ROLE_ADMIN not in new_roles:
            admins = await _repo().count_users(roles=[ROLE_ADMIN])
            if admins <= 1:
                raise HTTPException(
                    status_code=409, detail="Нельзя снять роль admin у последнего администратора"
                )
        updated = await _repo().update_user_roles(target.id, new_roles)
        return UserOut.model_validate(updated)

    @router.patch(
        "/api/users/{user_id}/status",
        response_model=UserOut,
        dependencies=[Depends(require_admin)],
    )
    async def update_status(
        user_id: int, body: UserStatusIn, admin: User | None = Depends(require_admin)
    ) -> UserOut:
        """Блокирует/разблокирует аккаунт (нельзя — себе)."""
        target = await _repo().get_user(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if admin is not None and target.id == admin.id:
            raise HTTPException(status_code=403, detail="Нельзя менять статус себе")
        updated = await _repo().set_user_status(target.id, body.status)
        return UserOut.model_validate(updated)

    @router.delete(
        "/api/users/{user_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    async def delete_user(user_id: int, admin: User | None = Depends(require_admin)) -> None:
        """Удаляет пользователя (нельзя — себе и последнему admin)."""
        target = await _repo().get_user(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if admin is not None and target.id == admin.id:
            raise HTTPException(status_code=403, detail="Нельзя удалить себя")
        if ROLE_ADMIN in target.roles:
            admins = await _repo().count_users(roles=[ROLE_ADMIN])
            if admins <= 1:
                raise HTTPException(
                    status_code=409, detail="Нельзя удалить последнего администратора"
                )
        await _repo().delete_user(target.id)

    return router
