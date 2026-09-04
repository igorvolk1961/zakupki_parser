"""Закупки, принятые тендерологом «в работу» (Эпик 5, US-5.4–5.6).

Признак «в работе» хранится на уровне ПРОФИЛЯ (``profile_id``, BR-07): профиль
принадлежит пользователю, изоляция — через владение профилем (как оценки
``procurement_evaluations``). Связь с закупкой (``procurement_id``) НЕ каскадная
(``ON DELETE SET NULL``): запись «в работе» переживает удаление закупки из общей
базы (например, очистку БД девопсом) — ключевые поля карточки сохраняются снимком
прямо в записи (``number/platform_id/url/subject/nmck/deadline/law/customer_name``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from zakupki_parser.storage.db.base import Base


class ProcurementWorkItem(Base):
    """Закупка пользователя/профиля, принятая в работу.

    ``source`` — откуда принята закупка: ``search`` (из результатов поиска) или
    ``url`` (по явно указанному URL ЭТП). ``status`` — задел под жизненный цикл
    работы (сейчас всегда ``in_work``). ``accepted_at`` — момент принятия.
    """

    __tablename__ = "procurement_work_items"
    __table_args__ = (
        UniqueConstraint("profile_id", "procurement_id", name="uq_work_items_profile_proc"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Закупка может быть удалена из общей базы (clear_all и т.п.): запись «в работе»
    # остаётся (FK SET NULL), карточка отдаётся из снимка ниже.
    procurement_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("procurements.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'search'"), default="search"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'in_work'"), default="in_work"
    )
    notes: Mapped[str | None] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Снимок ключевых полей карточки закупки на момент принятия (resilience при
    # удалении procurements). Живые значения берутся из procurements, если они есть.
    number: Mapped[str | None] = mapped_column(String(64))
    platform_id: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(1024))
    subject: Mapped[str | None] = mapped_column(Text)
    nmck: Mapped[float | None] = mapped_column(Float)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    law: Mapped[str | None] = mapped_column(String(16))
    customer_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
