"""Операции репозитория с пользователями (администратор/тендеролог)."""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from zakupki_parser.storage.db import Profile, User
from zakupki_parser.storage.repository.base import RepositoryMixin

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

    async def count_users(self, role: str | None = None) -> int:
        stmt = select(func.count(User.id))
        if role is not None:
            stmt = stmt.where(User.role == role)
        async with self._db.session() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def create_user(
        self, username: str, password_hash: str, role: str, email: str | None = None
    ) -> User:
        user = User(username=username, password_hash=password_hash, role=role, email=email)
        async with self._db.session() as session:
            session.add(user)
            await session.commit()
        logger.info("Создан пользователь %s (роль %s)", username, role)
        return user

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
