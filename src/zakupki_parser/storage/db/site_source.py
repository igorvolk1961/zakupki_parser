"""Сайты-источники значений условий отчётных полей (текст сайта в S3).

Одна строка — один нормализованный URL (``url_norm``): сколько бы профилей и
условий ни ссылались на сайт, он собирается один раз. Сам текст — в S3
(``scoring_common.sources.store``), здесь — статус и ход сбора.

``status``: ``pending`` (в очереди) -> ``running`` -> итог: ``complete``
(пагинация закончилась сама — ``stop_reason`` ``no_next``/``repeat``/
``no_change``), ``incomplete`` (упёрлись в лимит, отменено, не удалось
перейти на найденную следующую страницу — собранное сохранено, но полнота
не доказана), ``failed`` (не собрано ничего пригодного, ``error``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zakupki_parser.storage.db.base import Base

SOURCE_FINAL_STATUSES = ("complete", "incomplete", "failed")


class SiteSource(Base):
    """Сайт-источник и состояние его сбора."""

    __tablename__ = "site_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    url_norm: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Ход сбора: {pages, chars, current_url, mode, updated_at} — после каждой страницы.
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Текст, который сейчас лежит в хранилище: когда получен и получен ли он
    # полным сбором (пагинация закончилась сама). Не совпадает со ``status``,
    # если последний пересбор сорвался — остаётся текст прежнего сбора.
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
