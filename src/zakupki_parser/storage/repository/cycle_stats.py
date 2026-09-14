"""Сводка проходов планировщика (devops-мониторинг, вкладка «Мониторинг»).

Пишется ``Scheduler`` после каждого завершённого прохода (``run_once``/
``_run_refresh_pass``); читается ``GET /api/devops/monitoring`` — «последний
цикл» и «в среднем» по последним проходам.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from zakupki_parser.storage.db import ParserCycleStats
from zakupki_parser.storage.repository.base import RepositoryMixin

# Число последних regular-проходов, по которым считаются средние (devops-
# мониторинг): достаточно для устойчивого среднего, но не тянет всю историю
# таблицы на каждый опрос вкладки (обновляется раз в 10с, см. monitoring.js).
_AVERAGE_WINDOW = 20


class CycleStatsMixin(RepositoryMixin):
    """Операции с ``parser_cycle_stats``."""

    async def record_cycle_stats(
        self,
        *,
        iteration: int,
        kind: str,
        started_at: datetime,
        finished_at: datetime,
        platforms_total: int,
        platforms_failed: int,
        received: int,
        saved: int,
    ) -> None:
        """Пишет сводку одного завершённого прохода (best-effort — вызывающий сам
        решает, роняет ли сбой записи весь цикл; см. ``Scheduler.run_once``)."""
        duration_seconds = max(0.0, (finished_at - started_at).total_seconds())
        async with self._db.session() as session:
            session.add(
                ParserCycleStats(
                    iteration=iteration,
                    kind=kind,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=duration_seconds,
                    platforms_total=platforms_total,
                    platforms_failed=platforms_failed,
                    received=received,
                    saved=saved,
                )
            )
            await session.commit()

    async def cycle_stats_summary(self, kind: str = "regular") -> dict[str, Any]:
        """«Последний цикл» + «в среднем за последние N» для вкладки «Мониторинг».

        Среднее считается по последним ``_AVERAGE_WINDOW`` завершённым проходам
        заданного ``kind`` (подзапрос ``ORDER BY started_at DESC LIMIT N``), а не
        по всей истории — иначе многомесячная накопленная таблица медленно бы
        тянула среднее к давно неактуальному поведению площадок/профилей.
        """
        async with self._db.session() as session:
            last = (
                await session.execute(
                    select(ParserCycleStats)
                    .where(ParserCycleStats.kind == kind)
                    .order_by(ParserCycleStats.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            window = (
                select(ParserCycleStats.id)
                .where(ParserCycleStats.kind == kind)
                .order_by(ParserCycleStats.started_at.desc())
                .limit(_AVERAGE_WINDOW)
                .subquery()
            )
            agg = (
                await session.execute(
                    select(
                        func.count(),
                        func.avg(ParserCycleStats.duration_seconds),
                        func.avg(ParserCycleStats.received),
                        func.avg(ParserCycleStats.saved),
                        func.avg(ParserCycleStats.platforms_failed),
                    ).where(ParserCycleStats.id.in_(select(window.c.id)))
                )
            ).one()
        count, avg_duration, avg_received, avg_saved, avg_failed = agg
        return {
            "last": None
            if last is None
            else {
                "iteration": last.iteration,
                "started_at": last.started_at.isoformat(),
                "finished_at": last.finished_at.isoformat(),
                "duration_seconds": last.duration_seconds,
                "platforms_total": last.platforms_total,
                "platforms_failed": last.platforms_failed,
                "received": last.received,
                "saved": last.saved,
            },
            "average": None
            if not count
            else {
                "sample_size": int(count),
                "duration_seconds": float(avg_duration or 0.0),
                "received": float(avg_received or 0.0),
                "saved": float(avg_saved or 0.0),
                "platforms_failed": float(avg_failed or 0.0),
            },
        }
