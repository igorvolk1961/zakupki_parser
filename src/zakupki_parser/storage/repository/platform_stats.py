"""Статистика обхода по площадкам (devops-мониторинг, вкладка «Мониторинг»).

Пишется ``Scheduler`` после каждой обработки КАЖДОЙ площадки (см.
``Scheduler._process_platform``); читается ``GET /api/devops/platform-stats``
— таблица «по площадкам», с поиском и пагинацией (рассчитана на сотни
площадок, см. докстринг ``ParserPlatformStats``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql.elements import ColumnElement

from zakupki_parser.storage.db import ParserPlatformStats
from zakupki_parser.storage.repository.base import RepositoryMixin


class PlatformStatsMixin(RepositoryMixin):
    """Операции с ``parser_platform_stats``."""

    async def upsert_platform_stats(
        self,
        *,
        platform_id: str,
        iteration: int,
        started_at: datetime,
        finished_at: datetime,
        success: bool,
        received: int,
        saved: int,
        error_message: str | None = None,
    ) -> None:
        """Обновляет статистику одной площадки (upsert — см. докстринг модели).

        Best-effort: сбой записи не должен ронять обход площадки (вызывающий,
        ``Scheduler._record_platform_stats``, сам ловит исключения).
        """
        duration = max(0.0, (finished_at - started_at).total_seconds())
        error_text = error_message[:2000] if error_message else None
        async with self._db.session() as session:
            stmt = pg_insert(ParserPlatformStats).values(
                platform_id=platform_id,
                last_iteration=iteration,
                last_started_at=started_at,
                last_finished_at=finished_at,
                last_success=success,
                last_received=received,
                last_saved=saved,
                last_error=error_text,
                runs_total=1,
                runs_failed=0 if success else 1,
                sum_duration_seconds=duration,
                sum_received=received,
                sum_saved=saved,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["platform_id"],
                set_={
                    "last_iteration": stmt.excluded.last_iteration,
                    "last_started_at": stmt.excluded.last_started_at,
                    "last_finished_at": stmt.excluded.last_finished_at,
                    "last_success": stmt.excluded.last_success,
                    "last_received": stmt.excluded.last_received,
                    "last_saved": stmt.excluded.last_saved,
                    "last_error": stmt.excluded.last_error,
                    # Накопительные счётчики — прибавляем к уже сохранённому
                    # значению (не перезаписываем), см. докстринг модели.
                    "runs_total": ParserPlatformStats.runs_total + 1,
                    "runs_failed": ParserPlatformStats.runs_failed + (0 if success else 1),
                    "sum_duration_seconds": ParserPlatformStats.sum_duration_seconds + duration,
                    "sum_received": ParserPlatformStats.sum_received + received,
                    "sum_saved": ParserPlatformStats.sum_saved + saved,
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def list_platform_stats(
        self,
        *,
        search: str | None = None,
        only_failed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ParserPlatformStats], int]:
        """Список площадок с пагинацией/поиском (рассчитано на сотни площадок).

        ``search`` — подстрока ``platform_id`` (ILIKE), ``only_failed`` — только
        площадки, чья ПОСЛЕДНЯЯ обработка завершилась ошибкой (быстро найти
        проблемные среди множества площадок). Сортировка — по свежести
        (``last_finished_at`` убывание): недавно обработанные/упавшие видны
        первыми.
        """
        conditions: list[ColumnElement[bool]] = []
        if search:
            conditions.append(ParserPlatformStats.platform_id.ilike(f"%{search}%"))
        if only_failed:
            conditions.append(ParserPlatformStats.last_success.is_(False))
        stmt = (
            select(ParserPlatformStats)
            .where(*conditions)
            .order_by(ParserPlatformStats.last_finished_at.desc())
        )
        count_stmt = select(func.count()).select_from(ParserPlatformStats).where(*conditions)
        async with self._db.session() as session:
            rows = list((await session.execute(stmt.limit(limit).offset(offset))).scalars().all())
            total = (await session.execute(count_stmt)).scalar_one()
        return rows, total

    @staticmethod
    def platform_stats_out(row: ParserPlatformStats) -> dict[str, Any]:
        """Сериализация строки статистики площадки для API (среднее — из накопленных сумм)."""
        n = row.runs_total or 0
        return {
            "platform_id": row.platform_id,
            "last_iteration": row.last_iteration,
            "last_started_at": row.last_started_at.isoformat(),
            "last_finished_at": row.last_finished_at.isoformat(),
            "last_success": row.last_success,
            "last_received": row.last_received,
            "last_saved": row.last_saved,
            "last_error": row.last_error,
            "runs_total": row.runs_total,
            "runs_failed": row.runs_failed,
            "avg_duration_seconds": (row.sum_duration_seconds / n) if n else 0.0,
            "avg_received": (row.sum_received / n) if n else 0.0,
            "avg_saved": (row.sum_saved / n) if n else 0.0,
        }
