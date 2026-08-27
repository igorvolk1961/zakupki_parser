"""Зависимости API (авторизация) и общие хелперы роутеров.

Выделено из прежнего монолитного ``api/app.py``. ``build_context(state)`` создаёт
замыкания-зависимости и хелперы, связанные с состоянием приложения; каждый роутер
связывает нужные имена локально, сохраняя прежние тела обработчиков без изменений.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, HTTPException, Request

from zakupki_parser.api.app.schemas import (
    ConfirmationTypeOut,
    ExperienceOut,
    LicenseOut,
    LicenseTypeOut,
    ProfileFactsOut,
    ProfileIn,
    ProfileOut,
)
from zakupki_parser.api.app.state import AppState
from zakupki_parser.auth import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_DEVOPS,
    ROLE_USER,
    decode_token,
    hash_password,
)
from zakupki_parser.storage.db import Profile, User
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class ApiContext:
    """Контекст API: состояние приложения и набор связанных с ним хелперов.

    Атрибуты-функции заполняются в ``build_context`` как замыкания над ``state``.
    """

    state: AppState
    _repo: Callable[[], ProcurementRepository]
    _ensure_service_account: Callable[[], Awaitable[User]]
    _effective_user: Callable[[User | None], Awaitable[User]]
    _active_context: Callable[[User | None], Awaitable[tuple[User, Profile]]]
    _profile_out: Callable[..., Awaitable[ProfileOut]]
    _owned_profile: Callable[[User | None, int], Awaitable[Profile]]
    _license_types_map: Callable[[], Awaitable[dict[int, LicenseTypeOut]]]
    _confirmation_types_map: Callable[[], Awaitable[dict[int, ConfirmationTypeOut]]]
    _validate_profile_entries: Callable[[ProfileIn], Awaitable[None]]
    _license_out: Callable[[Any, dict[int, LicenseTypeOut]], LicenseOut]
    _experience_out: Callable[[Any, dict[int, ConfirmationTypeOut]], ExperienceOut]
    _extract_bearer: Callable[[Request], str | None]
    require_user: Callable[[Request], Awaitable[User | None]]
    require_admin: Callable[[User | None], User | None]
    require_analyst: Callable[[User | None], User | None]
    require_devops: Callable[[User | None], User | None]
    require_base: Callable[[User | None], User | None]
    require_internal: Callable[[Request], None]
    require_user_or_internal: Callable[[Request], Awaitable[User | None]]
    _auth_disabled: Callable[[], None]
    _seed_initial_admin: Callable[[], Awaitable[None]]

    def __init__(self, state: AppState) -> None:
        self.state = state


def build_context(state: AppState) -> ApiContext:
    """Создаёт контекст API: зависимости авторизации и общие хелперы роутеров."""
    ctx = ApiContext(state)

    def _repo() -> ProcurementRepository:
        if state.repository is None:
            raise HTTPException(status_code=503, detail="БД недоступна")
        return state.repository

    async def _ensure_service_account() -> User:
        """Сервис-аккаунт: первый пользователь (admin), осиротевшие профили — его.

        Используется в dev-режиме (auth off) и конвейером скоринга: профиль
        «активного клиента» теперь принадлежит пользователю (BR-07). Создаёт
        пользователя, если таблица пуста (env-сид ZAKUPKI_ADMIN_* или fallback
        «admin» со сгенерированным паролем), и присваивает профили без user_id.
        """
        cached: User | None = getattr(state, "service_account", None)
        if cached is not None:
            return cached
        user = await _repo().first_user()
        if user is not None:
            await _repo().backfill_orphaned_profiles(user.id)
        else:
            username = os.environ.get("ZAKUPKI_ADMIN_USERNAME") or "administrator"
            password = os.environ.get("ZAKUPKI_ADMIN_PASSWORD") or secrets.token_urlsafe(24)
            user = await _repo().create_user(
                username, await asyncio.to_thread(hash_password, password), list(ALL_ROLES)
            )
            logger.warning(
                "Создан сервис-аккаунт %s (пароль %s)",
                username,
                "из env" if os.environ.get("ZAKUPKI_ADMIN_PASSWORD") else "сгенерирован",
            )
            await _repo().backfill_orphaned_profiles(user.id)
        # Профиль default создаётся пустым (слова загружаются скриптом seed-profile, R8);
        # сервис-аккаунт имеет роль user/analyst — профиль положен.
        await _repo().ensure_default_profile(user.id, user.roles)
        state.service_account = user
        return user

    async def _effective_user(user: User | None) -> User:
        """Текущий пользователь; при выключенной авторизации — сервис-аккаунт."""
        if user is not None:
            return user
        return await _ensure_service_account()

    async def _active_context(user: User | None) -> tuple[User, Profile]:
        """Эффективный пользователь и его активный профиль (BR-07).

        Оценки (procurement_evaluations) ключуются по ``profile_id`` (оценки относятся
        к профилю), профиль — контекст фильтрации. Возвращает пару, чтобы не резолвить
        пользователя дважды.
        """
        eff_user = await _effective_user(user)
        profile = await _repo().get_active_profile(eff_user.id)
        if profile is None:
            raise HTTPException(
                status_code=503,
                detail="Активный профиль не найден (примените миграции)",
            )
        return eff_user, profile

    async def _profile_out(
        profile: Profile,
        keywords: dict[str, list[str]] | None = None,
        include_facts: bool = False,
    ) -> ProfileOut:
        """Карточка профиля со словами из таблицы ``keywords`` (канонический источник).

        ``include_facts`` — прикрепить факты BR-03 (лицензии/опыт) для конвейера
        (эндпоинт /api/clients/active): они нужны Stage B анализа ТЗ.
        """
        data = ProfileOut.model_validate(profile).model_dump()
        if keywords is None:
            keywords = await _repo().get_profile_keywords(profile.id)
        data["keywords"] = keywords["keywords"]
        data["exclusion_words"] = keywords["exclusion_words"]
        if include_facts:
            data["facts"] = ProfileFactsOut(**await _repo().get_profile_facts(profile.id))
        return ProfileOut(**data)

    async def _owned_profile(user: User | None, client_id: int) -> Profile:
        """Профиль пользователя или 404 (tenant-скоуп BR-07).

        Лицензии/опыт привязаны к профилю, профиль — к пользователю: проверка
        владения обязательна для всех вложенных эндпоинтов (как в get_client).
        """
        eff_user = await _effective_user(user)
        profile = await _repo().get_profile(eff_user.id, client_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        return profile

    async def _license_types_map() -> dict[int, LicenseTypeOut]:
        return {t.id: LicenseTypeOut.model_validate(t) for t in await _repo().list_license_types()}

    async def _confirmation_types_map() -> dict[int, ConfirmationTypeOut]:
        return {
            t.id: ConfirmationTypeOut.model_validate(t)
            for t in await _repo().list_confirmation_types()
        }

    async def _validate_profile_entries(body: ProfileIn) -> None:
        """Проверяет ссылки на справочники в списках лицензий/опыта профиля."""
        if body.licenses:
            license_map = await _license_types_map()
            if any(lic.license_type_id not in license_map for lic in body.licenses):
                raise HTTPException(status_code=422, detail="Неизвестный тип лицензии")
        if body.experience:
            confirmation_map = await _confirmation_types_map()
            if any(exp.confirmation_type_id not in confirmation_map for exp in body.experience):
                raise HTTPException(status_code=422, detail="Неизвестный тип подтверждения")

    def _license_out(row: Any, types_map: dict[int, LicenseTypeOut]) -> LicenseOut:
        out = LicenseOut.model_validate(row)
        out.license_type = types_map.get(row.license_type_id)
        return out

    def _experience_out(row: Any, types_map: dict[int, ConfirmationTypeOut]) -> ExperienceOut:
        out = ExperienceOut.model_validate(row)
        out.confirmation_type = types_map.get(row.confirmation_type_id)
        return out

    def _extract_bearer(request: Request) -> str | None:
        authz = request.headers.get("Authorization")
        if not authz or not authz.startswith("Bearer "):
            return None
        return authz[len("Bearer ") :].strip()

    async def require_user(request: Request) -> User | None:
        """Текущий пользователь по bearer-токену; None при выключенной авторизации."""
        if not state.cfg.ops.auth.enabled:
            return None
        token = _extract_bearer(request)
        if token is None:
            raise HTTPException(status_code=401, detail="Требуется авторизация")
        payload = decode_token(token, state.cfg.ops.auth.secret or "")
        if payload is None:
            raise HTTPException(status_code=401, detail="Недействительный или истёкший токен")
        user = await _repo().get_user(payload["sub"])
        if user is None:
            raise HTTPException(status_code=401, detail="Пользователь не найден")
        if user.status == "blocked":
            raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
        return user

    def _require_roles(*required: str) -> Callable[[User | None], User | None]:
        """Зависимость: пользователь с хотя бы одной из требуемых ролей.

        При выключенной авторизации (user=None) запрос пропускается — как в
        прежнем ``require_admin`` (dev-режим).
        """

        def require_roles(user: User | None = Depends(require_user)) -> User | None:
            if user is None:
                return None
            if not (set(user.roles) & set(required)):
                label = ", ".join(required)
                raise HTTPException(status_code=403, detail=f"Требуется одна из ролей: {label}")
            return user

        return require_roles

    require_admin = _require_roles(ROLE_ADMIN)
    require_analyst = _require_roles(ROLE_ANALYST)
    require_devops = _require_roles(ROLE_DEVOPS)
    # Базовые вкладки (Закупки/Заказчики/Профили) видят user и analyst.
    require_base = _require_roles(ROLE_USER, ROLE_ANALYST)

    def require_internal(request: Request) -> None:
        """Доступ только для внутренних сервисов конвейера (по X-Internal-Token).

        Применяется к служебным эндпоинтам (POST /score, POST /customers/{id}/rating),
        которые вызывают компоненты конвейера, а не пользователи. Fail-closed:
        при включённой авторизации без заданного токена эндпоинты закрыты
        (конфиг-валидатор отклоняет такой запуск ещё на старте; ветка — страховка).
        """
        if not state.cfg.ops.auth.enabled:
            return
        internal = state.cfg.ops.auth.internal_token
        if not internal:
            raise HTTPException(
                status_code=503,
                detail="Внутренний токен конвейера не задан (ZAKUPKI_INTERNAL_TOKEN)",
            )
        if request.headers.get("X-Internal-Token") != internal:
            raise HTTPException(status_code=401, detail="Неверный внутренний токен")

    async def require_user_or_internal(request: Request) -> User | None:
        """Пользователь ИЛИ внутренний токен конвейера (например, /api/clients/active).

        Конвейер скоринга (scoring_service/analysis_service) не имеет пользовательского
        токена, но читает активный профиль клиента: внутренний токен пропускает запрос.
        """
        if not state.cfg.ops.auth.enabled:
            return None
        internal = state.cfg.ops.auth.internal_token
        if internal and request.headers.get("X-Internal-Token") == internal:
            return None
        return await require_user(request)

    def _auth_disabled() -> None:
        if not state.cfg.ops.auth.enabled:
            raise HTTPException(status_code=404, detail="Авторизация отключена")

    async def _seed_initial_admin() -> None:
        """Создаёт первого администратора из env, если таблица пользователей пуста.

        Удобно для первого развёртывания (Docker): задайте ZAKUPKI_ADMIN_USERNAME и
        ZAKUPKI_ADMIN_PASSWORD; при наличии пользователей env игнорируется.
        """
        username = os.environ.get("ZAKUPKI_ADMIN_USERNAME")
        password = os.environ.get("ZAKUPKI_ADMIN_PASSWORD")
        if not username or not password:
            return
        if await _repo().count_users() > 0:
            return
        await _repo().create_user(username, hash_password(password), list(ALL_ROLES))
        logger.info("Создан начальный администратор %s (из env)", username)

    ctx._repo = _repo
    ctx._ensure_service_account = _ensure_service_account
    ctx._effective_user = _effective_user
    ctx._active_context = _active_context
    ctx._profile_out = _profile_out
    ctx._owned_profile = _owned_profile
    ctx._license_types_map = _license_types_map
    ctx._confirmation_types_map = _confirmation_types_map
    ctx._validate_profile_entries = _validate_profile_entries
    ctx._license_out = _license_out
    ctx._experience_out = _experience_out
    ctx._extract_bearer = _extract_bearer
    ctx.require_user = require_user
    ctx.require_admin = require_admin
    ctx.require_analyst = require_analyst
    ctx.require_devops = require_devops
    ctx.require_base = require_base
    ctx.require_internal = require_internal
    ctx.require_user_or_internal = require_user_or_internal
    ctx._auth_disabled = _auth_disabled
    ctx._seed_initial_admin = _seed_initial_admin
    return ctx
