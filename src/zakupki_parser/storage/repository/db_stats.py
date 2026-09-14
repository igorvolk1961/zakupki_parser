"""Размер БД на диске (devops-мониторинг, вкладка «Мониторинг»)."""

from __future__ import annotations

from sqlalchemy import text

from zakupki_parser.storage.repository.base import RepositoryMixin


class DbStatsMixin(RepositoryMixin):
    """Операции уровня БД, не привязанные к конкретной таблице/домену."""

    async def database_size_bytes(self) -> int:
        """Размер текущей БД на диске (``pg_database_size``, включая индексы/TOAST)."""
        async with self._db.session() as session:
            size = await session.scalar(text("SELECT pg_database_size(current_database())"))
        return int(size or 0)
