"""Операции репозитория с сайтами-источниками (``site_sources``)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zakupki_parser.storage.db import SiteSource
from zakupki_parser.storage.repository.base import RepositoryMixin


class SiteSourceMixin(RepositoryMixin):
    """Статус и ход сбора сайтов-источников."""

    async def get_site_source(self, source_id: int) -> SiteSource | None:
        async with self._db.session() as session:
            return await session.get(SiteSource, source_id)

    async def get_site_source_by_url(self, url_norm: str) -> SiteSource | None:
        async with self._db.session() as session:
            stmt = select(SiteSource).where(SiteSource.url_norm == url_norm)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_or_create_site_source(self, url: str, url_norm: str) -> SiteSource:
        """Источник по нормализованному URL; нет — создаётся в статусе ``pending``."""
        async with self._db.session() as session:
            await session.execute(
                pg_insert(SiteSource)
                .values(url=url, url_norm=url_norm, status="pending", progress={})
                .on_conflict_do_nothing(index_elements=["url_norm"])
            )
            await session.commit()
            stmt = select(SiteSource).where(SiteSource.url_norm == url_norm)
            return (await session.execute(stmt)).scalar_one()

    async def _update_site_source(self, source_id: int, **values: Any) -> None:
        async with self._db.session() as session:
            await session.execute(
                update(SiteSource).where(SiteSource.id == source_id).values(**values)
            )
            await session.commit()

    async def mark_site_source_pending(self, source_id: int) -> None:
        """Поставить (повторный) сбор в очередь; прежний текст остаётся доступен."""
        await self._update_site_source(
            source_id, status="pending", cancel_requested=False, error=None, progress={}
        )

    async def mark_site_source_running(self, source_id: int) -> None:
        await self._update_site_source(
            source_id, status="running", started_at=datetime.now(UTC), finished_at=None
        )

    async def update_site_source_progress(self, source_id: int, progress: dict[str, Any]) -> None:
        await self._update_site_source(source_id, progress=progress)

    async def finish_site_source(
        self,
        source_id: int,
        *,
        status: str,
        stop_reason: str | None,
        pages: int | None = None,
        text_chars: int | None = None,
        error: str | None = None,
        fetched: bool = False,
    ) -> None:
        """Итог сбора. ``fetched`` — в хранилище записан новый текст (``fetched_at``)."""
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "status": status,
            "stop_reason": stop_reason,
            "error": error,
            "finished_at": now,
            "cancel_requested": False,
        }
        if pages is not None:
            values["pages"] = pages
        if text_chars is not None:
            values["text_chars"] = text_chars
        if fetched:
            values["fetched_at"] = now
            values["text_complete"] = status == "complete"
        await self._update_site_source(source_id, **values)

    async def request_site_source_cancel(self, source_id: int) -> None:
        await self._update_site_source(source_id, cancel_requested=True)

    async def site_source_cancel_requested(self, source_id: int) -> bool:
        async with self._db.session() as session:
            stmt = select(SiteSource.cancel_requested).where(SiteSource.id == source_id)
            return bool((await session.execute(stmt)).scalar_one_or_none())

    async def recover_site_sources_after_restart(self) -> list[int]:
        """После рестарта API: прерванные сборы (``running``) — ``failed``,
        ожидающие (``pending``) — их id, чтобы поставить заново."""
        async with self._db.session() as session:
            await session.execute(
                update(SiteSource)
                .where(SiteSource.status == "running")
                .values(
                    status="failed",
                    stop_reason="error",
                    error="прервано перезапуском сервиса",
                    finished_at=datetime.now(UTC),
                )
            )
            pending = select(SiteSource.id).where(SiteSource.status == "pending")
            ids = [int(i) for i in (await session.execute(pending)).scalars()]
            await session.commit()
            return ids
