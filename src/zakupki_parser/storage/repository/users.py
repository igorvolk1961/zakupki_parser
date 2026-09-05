"""Операции репозитория с пользователями (роли user/admin/analyst/devops)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from zakupki_parser.auth import has_default_profile_role
from zakupki_parser.options import paid_default_options
from zakupki_parser.storage.db import Profile, User, UserAccount
from zakupki_parser.storage.repository.accounts import AccountMixin
from zakupki_parser.storage.repository.base import RepositoryMixin
from zakupki_parser.storage.repository.profiles import ProfileMixin

logger = logging.getLogger(__name__)


class UserMixin(RepositoryMixin):
    """Пользователи (``users``) и привязка осиротевших профилей."""

    async def get_user(self, user_id: int) -> User | None:
        async with self._db.session() as session:
            return await session.get(User, user_id)

    async def get_user_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def count_users(self, roles: list[str] | None = None) -> int:
        stmt = select(func.count(User.id))
        if roles:
            stmt = stmt.where(User.roles.overlap(roles))
        async with self._db.session() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def list_users(self) -> list[User]:
        """Все пользователи сервиса (для админ-вкладки «Пользователи»)."""
        stmt = select(User).order_by(User.id.asc())
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars())

    async def create_user(
        self, username: str, password_hash: str, roles: list[str], email: str | None = None
    ) -> User:
        user = User(username=username, password_hash=password_hash, roles=roles, email=email)
        async with self._db.session() as session:
            session.add(user)
            await session.commit()
        logger.info("Создан пользователь %s (роли %s)", username, ",".join(roles))
        return user

    async def create_user_with_setup(
        self,
        username: str,
        password_hash: str,
        roles: list[str],
        email: str | None = None,
        *,
        trial_end_at: datetime | None = None,
        account_paid_default: bool = False,
    ) -> User:
        """Регистрирует пользователя одной транзакцией: пользователь + триал.

        В том же коммите создаются default-профиль (для ролей user/analyst, как
        ``ensure_default_profile``) и активный default-аккаунт (как
        ``ensure_default_account``). Атомарность важна: если бы пользователь
        коммитился до выставления ``trial_end_at``/аккаунта, сбой между шагами
        оставлял бы пользователя без триала и без аккаунта — по правилам
        легаси-доступа это означало бы «вечный полный доступ» (см. ревью).
        """
        async with self._db.session() as session:
            user = User(username=username, password_hash=password_hash, roles=roles, email=email)
            user.trial_end_at = trial_end_at
            session.add(user)
            await session.flush()
            if has_default_profile_role(roles):
                session.add(
                    Profile(
                        name=ProfileMixin.DEFAULT_PROFILE_NAME,
                        user_id=user.id,
                        enabled=True,
                        is_active=True,
                        competencies="",
                    )
                )
            session.add(
                UserAccount(
                    user_id=user.id,
                    name=AccountMixin.DEFAULT_ACCOUNT_NAME,
                    options=paid_default_options(enabled=account_paid_default),
                    is_active=True,
                )
            )
            await session.commit()
            await session.refresh(user)
        logger.info(
            "Создан пользователь %s (роли %s, триал до %s)",
            username,
            ",".join(roles),
            trial_end_at,
        )
        return user

    async def update_user_roles(self, user_id: int, roles: list[str]) -> User | None:
        stmt = select(User).where(User.id == user_id)
        async with self._db.session() as session:
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                return None
            user.roles = list(roles)
            await session.commit()
            await session.refresh(user)
        logger.info("Обновлены роли пользователя %s: %s", user_id, ",".join(roles))
        return user

    async def set_user_status(self, user_id: int, status: str) -> User | None:
        stmt = select(User).where(User.id == user_id)
        async with self._db.session() as session:
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                return None
            user.status = status
            await session.commit()
            await session.refresh(user)
        logger.info("Изменён статус пользователя %s: %s", user_id, status)
        return user

    async def set_user_password(self, user_id: int, password_hash: str) -> User | None:
        """Устанавливает новый хэш пароля (смена пароля в личном кабинете)."""
        stmt = select(User).where(User.id == user_id)
        async with self._db.session() as session:
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                return None
            user.password_hash = password_hash
            await session.commit()
            await session.refresh(user)
        logger.info("Изменён пароль пользователя %s", user_id)
        return user

    async def delete_user(self, user_id: int) -> bool:
        stmt = select(User).where(User.id == user_id)
        async with self._db.session() as session:
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                return False
            await session.delete(user)
            await session.commit()
        logger.info("Удалён пользователь %s (профили/оценки — каскадом)", user_id)
        return True

    async def first_user(self) -> User | None:
        """Первый пользователь (сервис-аккаунт для dev-режима и конвейера)."""
        stmt = select(User).order_by(User.id.asc()).limit(1)
        async with self._db.session() as session:
            result: User | None = (await session.execute(stmt)).scalar_one_or_none()
            return result

    async def backfill_orphaned_profiles(self, user_id: int) -> int:
        """Присваивает профили без ``user_id`` указанному пользователю (идемпотентно).

        Нужно для миграции 1.29: существующие профили (глобальные) после перехода
        на мультитенантность не имеют владельца — сервис-аккаунт забирает их на старте.
        """
        async with self._db.session() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(Profile).where(Profile.user_id.is_(None)).values(user_id=user_id)
                ),
            )
            await session.commit()
        count = int(result.rowcount or 0)
        if count:
            logger.info(
                "Осиротевшие профили (user_id IS NULL) присвоены пользователю %s: %s",
                user_id,
                count,
            )
        return count
