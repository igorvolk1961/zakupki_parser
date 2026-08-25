"""Справочник заказчиков (ADR-4)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Float, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zakupki_parser.storage.db.base import Base

if TYPE_CHECKING:
    from zakupki_parser.storage.db.procurement import Procurement


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
