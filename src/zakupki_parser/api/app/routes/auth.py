"""Эндпоинты авторизации: вход / выход / текущий пользователь / регистрация."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from zakupki_parser.auth import ROLE_TENDEROLOGIST, create_token, hash_password, verify_password
from zakupki_parser.storage.db import User

logger = logging.getLogger(__name__)


def build_auth_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    _auth_disabled = ctx._auth_disabled
    require_user = ctx.require_user

    @router.post("/api/auth/login", response_model=TokenOut)
    async def login(body: LoginIn) -> TokenOut:
        """Вход по логину и паролю: возвращает bearer-токен и профиль пользователя."""
        _auth_disabled()
        user = await _repo().get_user_by_username(body.username)
        # PBKDF2 (600k итераций) — CPU-bound: не блокируем event loop (~190 мс).
        ok = user is not None and await asyncio.to_thread(
            verify_password, body.password, user.password_hash
        )
        if user is None or not ok:
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        ttl = state.cfg.ops.auth.token_ttl_seconds
        token = create_token(user.id, user.role, state.cfg.ops.auth.secret or "", ttl)
        logger.info("Вход пользователя %s (роль %s)", user.username, user.role)
        return TokenOut(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))

    @router.post("/api/auth/logout", include_in_schema=False)
    async def logout(user: User | None = Depends(require_user)) -> dict[str, str]:
        """Выход (stateless: клиент удаляет токен; серверная сессия не ведётся)."""
        _auth_disabled()
        return {"status": "ok"}

    @router.get("/api/auth/me", response_model=UserOut)
    async def me(user: User | None = Depends(require_user)) -> UserOut:
        """Текущий пользователь. 404 — авторизация отключена (клиент не логинится)."""
        if user is None:
            raise HTTPException(status_code=404, detail="Авторизация отключена")
        return UserOut.model_validate(user)

    @router.post("/api/auth/register", response_model=TokenOut)
    async def register(body: RegisterIn) -> TokenOut:
        """Самостоятельная регистрация: пользователь сам выбирает пароль.

        Роль при регистрации всегда ``tenderologist``. Роль администратора
        регистрацией не выдаётся — её задаёт администратор системы (env-сид
        ``ZAKUPKI_ADMIN_USERNAME``/``ZAKUPKI_ADMIN_PASSWORD`` при первом старте
        либо правка таблицы ``users``).
        """
        _auth_disabled()
        if await _repo().get_user_by_username(body.username) is not None:
            raise HTTPException(status_code=409, detail="Пользователь с таким логином уже есть")
        password_hash = await asyncio.to_thread(hash_password, body.password)
        try:
            user = await _repo().create_user(
                body.username, password_hash, ROLE_TENDEROLOGIST, email=body.email
            )
        except IntegrityError as exc:
            # Гонка двух одновременных регистраций с одним логином: констрейнт
            # uq_users_username срабатывает позже pre-check — отдаём 409, а не 500.
            raise HTTPException(
                status_code=409, detail="Пользователь с таким логином уже есть"
            ) from exc
        # Каждому новому пользователю — активный профиль default (BR-07): без него
        # список закупок недоступен (нет контекста фильтрации). Профиль создаётся
        # пустым — ключевые слова/компетенции загружаются скриптом seed-profile (R8).
        await _repo().seed_default_profile(
            user.id,
            {
                "name": "default",
                "enabled": True,
                "is_active": True,
                "competencies": "",
                "keywords": [],
                "exclusion_words": [],
                "questions": [],
            },
        )
        ttl = state.cfg.ops.auth.token_ttl_seconds
        token = create_token(user.id, user.role, state.cfg.ops.auth.secret or "", ttl)
        logger.info("Зарегистрирован пользователь %s (роль %s)", user.username, user.role)
        return TokenOut(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))

    return router
