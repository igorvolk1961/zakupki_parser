"""Пользователи сервиса: администратор или тендеролог."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zakupki_parser.storage.db.base import Base

if TYPE_CHECKING:
    from zakupki_parser.storage.db.profile import Profile


class User(Base):
    """Пользователь сервиса: администратор или тендеролог.

    Роли: ``admin`` — управление сервисом (парсер, конфиги, пользователи, очистка БД);
    ``tenderologist`` — работа с закупками (просмотр, анализ). Каждый пользователь —
    отдельный tenant (BR-07): профили фильтрации и оценки принадлежат ``user_id``.
    Пока вход по логину/паролю (пароль — PBKDF2-хэш, см. ``zakupki_parser.auth``);
    позже — OAuth2 через Сбер ID.
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profiles: Mapped[list[Profile]] = relationship(back_populates="user_rel")
