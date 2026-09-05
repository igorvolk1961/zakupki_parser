"""Аккаунты пользователей: именованные наборы опций (что доступно пользователю).

Аккаунт описывает возможности пользователя: для каждой платной операции (см.
``zakupki_parser.options``) пользователь выбирает, доступна она ему или нет.
В каждый момент активен только один аккаунт пользователя (``is_active``);
пользователь сам переключает активный аккаунт в личном кабинете, администратор
может редактировать аккаунты любых пользователей.

``options`` — JSONB ``{ключ_опции: bool}``: хранятся только переключатели
платных опций (бесплатные доступны всегда и в БД не дублируются). По умолчанию
включаются только бесплатные опции; существующие пользователи при миграции
получают «полный» аккаунт (платные включены), чтобы не сломать текущее
поведение.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zakupki_parser.storage.db.base import Base

if TYPE_CHECKING:
    from zakupki_parser.storage.db.user import User


class UserAccount(Base):
    """Аккаунт пользователя: набор включённых платных опций.

    ``name`` уникален в пределах пользователя (как профили); активным может быть
    только один аккаунт (гарантируется логикой активации в репозитории).
    """

    __tablename__ = "user_accounts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_accounts_user_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict[str, bool]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user_rel: Mapped[User] = relationship(back_populates="accounts")
