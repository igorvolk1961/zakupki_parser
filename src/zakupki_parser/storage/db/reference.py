"""Глобальные справочники-классификаторы (не привязаны к профилю/пользователю)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from zakupki_parser.storage.db.base import Base


class Okpd2Code(Base):
    """Справочник ОКПД2 (ОК 034-2014 (КПЕС 2008)): код + наименование, без иерархии.

    Источник данных — сторонний открытый датасет (github.com/prog815/okpd2), не
    официальный API Росстандарта; для юридически точных сверок критичные коды
    стоит перепроверять по первоисточнику. Заполняется миграцией (``loadData``,
    ``db.changelog-1.71.yaml``), не приложением — справочник статичен, ~20300
    записей, идемпотентный сид на старте (как у ``LicenseType``) тут избыточен.
    ``parent_code``/``level`` сознательно не хранятся: сегменты кода между
    уровнями (класс/подкласс/группа/…) переменной длины, простое усечение по
    точке дало бы неверные связи для части записей.
    """

    __tablename__ = "okpd2_codes"
    __table_args__ = (UniqueConstraint("code", name="uq_okpd2_codes_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
