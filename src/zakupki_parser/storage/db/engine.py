"""Подключение к БД (PostgreSQL): тонкая обёртка над async engine/session."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from zakupki_parser.config.models import DbConfig


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
