"""Статистика обхода по площадкам (devops-мониторинг, вкладка «Мониторинг»).

Одна строка НА ПЛОЩАДКУ (upsert, не insert-per-cycle) — размер таблицы не
растёт с числом циклов, только с числом площадок (сейчас 10, в перспективе
сотни: см. ``ParserPlatformStats``) — принципиально при любом масштабе.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from zakupki_parser.storage.db.base import Base


class ParserPlatformStats(Base):
    """Одна строка — текущее состояние + накопительная статистика ОДНОЙ площадки.

    Обновляется (upsert по ``platform_id``) после каждой обработки площадки
    (``Scheduler._process_platform``) — успешной или нет. В отличие от
    ``ParserCycleStats`` (insert новой строки на каждый цикл, среднее — по
    последним N строкам), здесь ИМЕННО upsert: при сотнях площадок insert на
    каждую площадку на каждом цикле дал бы неограниченный рост таблицы; upsert
    держит ровно одну строку на площадку навсегда, а среднее считается из
    накопительных сумм (``sum_*``/``runs_total``) — без истории, O(1) на
    обновление независимо от того, сколько циклов уже прошло.

    ``last_success=False`` — последняя обработка завершилась исключением
    (см. ``Scheduler._process_platform``, включая ``CircuitOpenError``);
    ``last_error`` — текст ошибки (обрезан до разумной длины при записи).
    """

    __tablename__ = "parser_platform_stats"
    __table_args__ = (
        Index("ix_parser_platform_stats_last_finished", "last_finished_at"),
        Index("ix_parser_platform_stats_last_success", "last_success"),
    )

    platform_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    last_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    # Накопительные счётчики за всё время (не окно последних N, как у
    # ParserCycleStats) — среднее = sum_*/runs_total, дёшево при любом масштабе.
    runs_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    runs_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sum_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sum_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sum_saved: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
