"""Эндпоинты авторизации: вход / выход / текущий пользователь / регистрация."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from zakupki_parser.auth import ROLE_USER, create_token, hash_password, verify_password
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)


def build_auth_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    require_user = ctx.require_user

    @router.post("/api/auth/login", response_model=TokenOut)
    async def login(body: LoginIn) -> TokenOut:
        """Вход по логину и паролю: возвращает bearer-токен и профиль пользователя."""
        user = await _repo().get_user_by_username(body.username)
        # PBKDF2 (600k итераций) — CPU-bound: не блокируем event loop (~190 мс).
        ok = user is not None and await asyncio.to_thread(
            verify_password, body.password, user.password_hash
        )
        if user is None or not ok:
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        if user.status == "blocked":
            raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
        ttl = state.cfg.ops.auth.token_ttl_seconds
        token = create_token(user.id, user.roles, state.cfg.ops.auth.secret or "", ttl)
        logger.info("Вход пользователя %s (роли %s)", user.username, ",".join(user.roles))
        return TokenOut(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))

    @router.post("/api/auth/logout", include_in_schema=False)
    async def logout(user: User | None = Depends(require_user)) -> dict[str, str]:
        """Выход (stateless: клиент удаляет токен; серверная сессия не ведётся)."""
        return {"status": "ok"}

    @router.get("/api/auth/me", response_model=UserOut)
    async def me(user: User | None = Depends(require_user)) -> UserOut:
        """Текущий пользователь (по bearer-токену)."""
        return UserOut.model_validate(user)

    @router.post("/api/auth/register", response_model=TokenOut)
    async def register(body: RegisterIn) -> TokenOut:
        """Самостоятельная регистрация: пользователь сам выбирает пароль.

        Роль при регистрации всегда ``user`` (простой пользователь). Роли
        admin/analyst/devops регистрацией не выдаются — их задаёт администратор
        во вкладке «Пользователи».
        """
        if await _repo().get_user_by_username(body.username) is not None:
            raise HTTPException(status_code=409, detail="Пользователь с таким логином уже есть")
        password_hash = await asyncio.to_thread(hash_password, body.password)
        try:
            user = await _repo().create_user(
                body.username, password_hash, [ROLE_USER], email=body.email
            )
        except IntegrityError as exc:
            # Гонка двух одновременных регистраций с одним логином: констрейнт
            # uq_users_username срабатывает позже pre-check — отдаём 409, а не 500.
            raise HTTPException(
                status_code=409, detail="Пользователь с таким логином уже есть"
            ) from exc
        # Новому пользователю с ролью user/analyst — активный профиль default (BR-07):
        # без него список закупок недоступен (нет контекста фильтрации). Профиль
        # создаётся пустым — ключевые слова/компетенции загружаются seed-profile (R8).
        await _repo().ensure_default_profile(user.id, user.roles)
        ttl = state.cfg.ops.auth.token_ttl_seconds
        token = create_token(user.id, user.roles, state.cfg.ops.auth.secret or "", ttl)
        logger.info(
            "Зарегистрирован пользователь %s (роли %s)",
            user.username,
            ",".join(user.roles),
        )
        return TokenOut(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))

    return router
