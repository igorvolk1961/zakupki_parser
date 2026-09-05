"""Личный кабинет пользователя: аккаунты (наборы опций) и смена пароля.

Вход в кабинет — по кнопке «Кабинет» в шапке обычного интерфейса. Разделы:
- аккаунты: список, создание, переименование, смена активного, удаление;
- опции: что доступно пользователю (бесплатные всегда; платные — по аккаунту
  или в триал-режиме);
- смена пароля.

Активный аккаунт единственный (per-user); пользователь сам переключает его.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    AccountIn,
    AccountOut,
    AccountUpdateIn,
    CabinetOut,
    OptionOut,
    PasswordChangeIn,
    TrialStatusOut,
    UserOut,
)
from zakupki_parser.auth import hash_password, verify_password
from zakupki_parser.options import ALL_OPTIONS, GROUP_FREE, OptionDef
from zakupki_parser.storage.db import User
from zakupki_parser.storage.repository.accounts import EffectiveOptions, effective_options

logger = logging.getLogger(__name__)


def _trial_out(user: User, now: datetime) -> TrialStatusOut:
    if user.trial_end_at is None or user.trial_end_at <= now:
        return TrialStatusOut(enabled=False, trial_end_at=user.trial_end_at, days_left=None)
    delta = (user.trial_end_at - now).total_seconds()
    return TrialStatusOut(
        enabled=True,
        trial_end_at=user.trial_end_at,
        days_left=max(0, int(math.ceil(delta / 86400))),
    )


def _option_out(
    option: OptionDef, eff: EffectiveOptions, active_options: dict[str, bool]
) -> OptionOut:
    is_free = option.group == GROUP_FREE
    enabled = is_free or option.key in eff.paid_enabled
    account_enabled = None if is_free else bool(active_options.get(option.key, False))
    return OptionOut(
        key=option.key,
        title=option.title,
        description=option.description,
        group=option.group,
        available=option.available,
        requires_competencies=option.requires_competencies,
        enabled=enabled,
        account_enabled=account_enabled,
    )


def build_account_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _repo = ctx._repo
    require_user = ctx.require_user

    async def _cabinet(user: User) -> CabinetOut:
        accounts = await _repo().list_accounts(user.id)
        now = datetime.now(UTC)
        eff = effective_options(accounts, user.trial_end_at, now=now)
        active = eff.active_account
        active_options: dict[str, bool] = dict(active.options) if active is not None else {}
        catalog = [_option_out(option, eff, active_options) for option in ALL_OPTIONS]
        return CabinetOut(
            user_id=user.id,
            username=user.username,
            email=user.email,
            roles=list(user.roles),
            trial=_trial_out(user, now),
            active_account_id=active.id if active is not None else None,
            accounts=[AccountOut.model_validate(a) for a in accounts],
            catalog=catalog,
        )

    @router.get(
        "/api/account/cabinet",
        response_model=CabinetOut,
        dependencies=[Depends(require_user)],
    )
    async def cabinet(user: User | None = Depends(require_user)) -> CabinetOut:
        """Личный кабинет: триал, аккаунты и каталог опций с доступностью."""
        assert user is not None
        return await _cabinet(user)

    @router.get(
        "/api/account/catalog",
        response_model=list[OptionOut],
        dependencies=[Depends(require_user)],
    )
    async def catalog(user: User | None = Depends(require_user)) -> list[OptionOut]:
        """Каталог опций (без привязки к пользователю): для админ-редактора.

        ``enabled`` здесь означает «опция реализована системой» (для отложенных,
        напр. geo_premium, — False); фактическая доступность пользователю — в
        ``/api/account/cabinet`` и ``/api/users/{id}/accounts``.
        """
        return [
            OptionOut(
                key=option.key,
                title=option.title,
                description=option.description,
                group=option.group,
                available=option.available,
                requires_competencies=option.requires_competencies,
                enabled=option.available,
                account_enabled=None,
            )
            for option in ALL_OPTIONS
        ]

    @router.post(
        "/api/account/accounts",
        response_model=AccountOut,
        status_code=201,
        dependencies=[Depends(require_user)],
    )
    async def create_account(
        body: AccountIn, user: User | None = Depends(require_user)
    ) -> AccountOut:
        """Создаёт аккаунт (становится активным, если активного ещё нет)."""
        assert user is not None
        try:
            account = await _repo().create_account(user.id, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AccountOut.model_validate(account)

    @router.put(
        "/api/account/accounts/{account_id}",
        response_model=AccountOut,
        dependencies=[Depends(require_user)],
    )
    async def update_account(
        account_id: int, body: AccountUpdateIn, user: User | None = Depends(require_user)
    ) -> AccountOut:
        """Переименовывает аккаунт и/или меняет включённые платные опции."""
        assert user is not None
        try:
            account = await _repo().update_account(
                user.id, account_id, name=body.name, options=body.options
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if account is None:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
        return AccountOut.model_validate(account)

    @router.post(
        "/api/account/accounts/{account_id}/activate",
        response_model=AccountOut,
        dependencies=[Depends(require_user)],
    )
    async def activate_account(
        account_id: int, user: User | None = Depends(require_user)
    ) -> AccountOut:
        """Делает аккаунт активным (пользователь сам меняет активный аккаунт)."""
        assert user is not None
        try:
            account = await _repo().set_active_account(user.id, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AccountOut.model_validate(account)

    @router.delete(
        "/api/account/accounts/{account_id}",
        status_code=204,
        dependencies=[Depends(require_user)],
    )
    async def delete_account(account_id: int, user: User | None = Depends(require_user)) -> None:
        """Удаляет аккаунт (нельзя удалить последний или активный)."""
        assert user is not None
        try:
            await _repo().delete_account(user.id, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post(
        "/api/account/password",
        response_model=UserOut,
        dependencies=[Depends(require_user)],
    )
    async def change_password(
        body: PasswordChangeIn, user: User | None = Depends(require_user)
    ) -> UserOut:
        """Смена собственного пароля (проверяется текущий пароль)."""
        assert user is not None
        ok = await asyncio.to_thread(verify_password, body.current_password, user.password_hash)
        if not ok:
            raise HTTPException(status_code=403, detail="Неверный текущий пароль")
        new_hash = await asyncio.to_thread(hash_password, body.new_password)
        updated = await _repo().set_user_password(user.id, new_hash)
        assert updated is not None
        logger.info("Пользователь %s сменил пароль", user.username)
        return UserOut.model_validate(updated)

    return router
