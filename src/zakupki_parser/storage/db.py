"""SQLAlchemy 2.x модели и работа с БД (PostgreSQL)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from zakupki_parser.config.models import DbConfig


class Base(DeclarativeBase):
    pass


class Procurement(Base):
    """Запись о закупке."""

    __tablename__ = "procurements"
    __table_args__ = (
        UniqueConstraint("number", "source_platform", name="uq_procurement_number_platform"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    source_platform: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024))
    customer: Mapped[str | None] = mapped_column(Text)
    law: Mapped[str | None] = mapped_column(String(16))
    subject: Mapped[str | None] = mapped_column(Text)
    nmck: Mapped[float | None] = mapped_column(Float)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dates: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_term: Mapped[str | None] = mapped_column(Text)
    okpd2_codes: Mapped[str | None] = mapped_column(Text)
    kpgz_codes: Mapped[str | None] = mapped_column(Text)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Database:
    """Тонкая обёртка над SQLAlchemy async engine/session."""

    def __init__(self, cfg: DbConfig) -> None:
        self._cfg = cfg
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._cfg.dsn,
            pool_size=self._cfg.pool_max,
            max_overflow=0,
            pool_pre_ping=True,
            connect_args={
                "timeout": self._cfg.connect_timeout_seconds,
            },
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        if self._session_factory is None:
            raise RuntimeError("БД не подключена")
        return self._session_factory()

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    @property
    def is_connected(self) -> bool:
        return self._engine is not None
