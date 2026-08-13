"""SQLAlchemy 2.x модели и работа с БД (PostgreSQL)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from zakupki_parser.config.models import DbConfig


class Base(DeclarativeBase):
    pass


class Customer(Base):
    """Справочник заказчиков (ADR-4)."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_customers_normalized_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    inn: Mapped[str | None] = mapped_column(String(12))
    rating: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    procurements: Mapped[list[Procurement]] = relationship(back_populates="customer_rel")


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
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="SET NULL")
    )
    law: Mapped[str | None] = mapped_column(String(16))
    subject: Mapped[str | None] = mapped_column(Text)
    nmck: Mapped[float | None] = mapped_column(Float)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    update_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_term: Mapped[str | None] = mapped_column(Text)
    security_amount: Mapped[float | None] = mapped_column(Float)
    security_amount_unit: Mapped[str | None] = mapped_column(String(16))
    advance: Mapped[float | None] = mapped_column(Float)
    okpd2_codes: Mapped[str | None] = mapped_column(Text)
    kpgz_codes: Mapped[str | None] = mapped_column(Text)
    technical_spec_url: Mapped[str | None] = mapped_column(Text)
    technical_spec_name: Mapped[str | None] = mapped_column(Text)
    files_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    score: Mapped[float | None] = mapped_column(Float)
    fit_score: Mapped[float | None] = mapped_column(Float)
    score_method: Mapped[str | None] = mapped_column(String(64))
    # Ветка векторной близости (Giga Embedder): косинусная близость 0..1 текста
    # компетенций и описания закупки. None, если ветка выключена/не настроена/сбой.
    embedding_similarity: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer_rel: Mapped[Customer | None] = relationship(back_populates="procurements")


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
