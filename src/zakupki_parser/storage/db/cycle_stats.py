"""Сводка одного прохода планировщика (devops-мониторинг, вкладка «Мониторинг»)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from zakupki_parser.storage.db.base import Base


class ParserCycleStats(Base):
    """Одна строка — один завершённый проход ``Scheduler.run_once``/``_run_refresh_pass``.

    ``kind`` — ``regular`` (регулярный проход по расписанию, ``timeout_seconds``)
    или ``refresh`` (внеочередной обход изменённых профилей, fast-start) — вкладка
    «Мониторинг» агрегирует «последний цикл»/«в среднем» только по ``regular``,
    чтобы редкие точечные внеочередные обходы не искажали типичную длительность.

    ``platforms_failed`` — число площадок, обработка которых в этом проходе
    завершилась исключением (``Scheduler._process_platform`` перехватывает и
    логирует, не роняя весь проход — здесь фиксируется факт для мониторинга).
    """

    __tablename__ = "parser_cycle_stats"
    __table_args__ = (Index("ix_parser_cycle_stats_kind_started", "kind", "started_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    platforms_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    platforms_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
