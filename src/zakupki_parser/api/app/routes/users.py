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
import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    AccountIn,
    AccountOut,
    AccountUpdateIn,
    UserAccountsOut,
    UserIn,
    UserOut,
    UserRolesIn,
    UsersListOut,
    UserStatusIn,
    UserTrialIn,
)
from zakupki_parser.auth import ROLE_ADMIN, ROLE_USER, hash_password
from zakupki_parser.storage.db import User
from zakupki_parser.storage.repository.accounts import in_trial_now

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
            # Атомарно: пользователь + default-профиль + default-аккаунт. Аккаунт
            # «По умолчанию» — со всеми платными опциями: пользователя создаёт
            # администратор (осознанное предоставление доступа), поэтому поведение
            # не меняется — ограничить опции админ может в этом же интерфейсе (#5).
            user = await _repo().create_user_with_setup(
                body.username,
                password_hash,
                list(body.roles),
                email=body.email,
                trial_end_at=None,
                account_paid_default=True,
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="Пользователь с таким логином уже есть"
            ) from exc
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
        if updated is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        # Смена ролей пересекает границу «профиль положен»: при появлении
        # роли user/analyst создаём default-профиль, при исчезновении — удаляем.
        profile = await _repo().ensure_default_profile(target.id, updated.roles)
        if profile is None:
            await _repo().delete_profiles_without_default_role()
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

    async def _target_user(user_id: int) -> User:
        """Пользователь-цель админ-действия или 404."""
        target = await _repo().get_user(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        return target

    @router.patch(
        "/api/users/{user_id}/trial",
        response_model=UserOut,
        dependencies=[Depends(require_admin)],
    )
    async def update_trial(
        user_id: int, body: UserTrialIn, admin: User | None = Depends(require_admin)
    ) -> UserOut:
        """Управляет триал-режимом пользователя (выдать/продлить/отключить).

        ``days`` — триал на N суток от сейчас; ``trial_end_at`` — конкретная дата;
        ни то, ни другое — триал отключается (пользователь переходит на аккаунт).
        """
        target = await _target_user(user_id)
        if admin is not None and target.id == admin.id:
            # Себе триал админ тоже может выдать; запрета нет.
            pass
        if body.days is not None:
            trial_end_at = datetime.now(UTC) + timedelta(days=body.days)
        elif body.trial_end_at is not None:
            trial_end_at = body.trial_end_at
            if trial_end_at.tzinfo is None:
                trial_end_at = trial_end_at.replace(tzinfo=UTC)
        else:
            trial_end_at = None
        updated = await _repo().set_user_trial_end(target.id, trial_end_at)
        return UserOut.model_validate(updated)

    @router.get(
        "/api/users/{user_id}/accounts",
        response_model=UserAccountsOut,
        dependencies=[Depends(require_admin)],
    )
    async def list_user_accounts(user_id: int) -> UserAccountsOut:
        """Аккаунты пользователя и состояние триала (для админ-вкладки)."""
        target = await _target_user(user_id)
        accounts = await _repo().list_accounts(target.id)
        active_account_id = next((a.id for a in accounts if a.is_active), None)
        trial_enabled = in_trial_now(target.trial_end_at)
        days_left = None
        if trial_enabled and target.trial_end_at is not None:
            seconds = (target.trial_end_at - datetime.now(UTC)).total_seconds()
            days_left = max(0, int(math.ceil(seconds / 86400)))
        return UserAccountsOut(
            user_id=target.id,
            username=target.username,
            active_account_id=active_account_id,
            accounts=[AccountOut.model_validate(a) for a in accounts],
            trial={
                "enabled": trial_enabled,
                "trial_end_at": target.trial_end_at,
                "days_left": days_left,
            },
        )

    @router.post(
        "/api/users/{user_id}/accounts",
        response_model=AccountOut,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    async def create_user_account(user_id: int, body: AccountIn) -> AccountOut:
        """Создаёт аккаунт пользователю (становится активным, если активного нет)."""
        await _target_user(user_id)
        try:
            account = await _repo().create_account(user_id, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AccountOut.model_validate(account)

    @router.put(
        "/api/users/{user_id}/accounts/{account_id}",
        response_model=AccountOut,
        dependencies=[Depends(require_admin)],
    )
    async def update_user_account(
        user_id: int, account_id: int, body: AccountUpdateIn
    ) -> AccountOut:
        """Обновляет имя/опции аккаунта пользователя."""
        try:
            account = await _repo().update_account(
                user_id, account_id, name=body.name, options=body.options
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if account is None:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
        return AccountOut.model_validate(account)

    @router.post(
        "/api/users/{user_id}/accounts/{account_id}/activate",
        response_model=AccountOut,
        dependencies=[Depends(require_admin)],
    )
    async def activate_user_account(user_id: int, account_id: int) -> AccountOut:
        """Делает аккаунт пользователя активным."""
        try:
            account = await _repo().set_active_account(user_id, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AccountOut.model_validate(account)

    @router.delete(
        "/api/users/{user_id}/accounts/{account_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    async def delete_user_account(user_id: int, account_id: int) -> None:
        """Удаляет аккаунт пользователя (нельзя удалить последний/активный)."""
        try:
            await _repo().delete_account(user_id, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
