"""Поисковый индекс закупок: описание + текст документов (фоновая индексация).

Ограничен конфигурируемым диапазоном ОКПД2 (``IndexingConfig``, системный
«индексный» профиль, ``scheduler.py``): заполняется фоновым сервисом
``indexing_service`` и используется для мгновенного ответа «горячего» пересбора
(tsquery по ``search_tsv``) вместо живого повторного обхода площадок.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zakupki_parser.storage.db.base import Base

if TYPE_CHECKING:
    from zakupki_parser.storage.db.procurement import Procurement


class ProcurementSearchIndex(Base):
    """Индекс одной закупки: сырой текст описания+документов и tsvector для поиска.

    Один-к-одному с ``procurements`` (``UniqueConstraint`` на ``procurement_id``).
    ``search_tsv`` — генерируемая Postgres-колонка (``GENERATED ALWAYS AS ... STORED``,
    DDL — в Liquibase-миграции, НЕ здесь): приложение её не пишет, только читает/
    матчит через GIN-индекс (``parser/filtering_tsquery.py``). ``status`` — жизненный
    цикл индексации одной закупки: ``pending`` (запись создана, файлы ещё не
    обработаны) -> ``indexed`` (текст извлечён, ``search_tsv`` актуален) | ``error``
    (сбой скачивания/извлечения — ``error_message``, повтор на следующей итерации).
    """

    __tablename__ = "procurement_search_index"
    __table_args__ = (
        UniqueConstraint("procurement_id", name="uq_procurement_search_index_procurement"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    procurement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("procurements.id", ondelete="CASCADE"), nullable=False
    )
    # Нормализованный код ОКПД2 закупки (см. okpd.py::normalize_okpd_codes) —
    # денормализация для быстрой фильтрации по диапазону без похода в procurements.
    okpd2_normalized: Mapped[str | None] = mapped_column(Text)
    # Снимок subject закупки на момент индексации (денормализация: GENERATED-колонка
    # Postgres не может ссылаться на другую таблицу — search_tsv строится из полей
    # ТОЛЬКО этой таблицы). Обновляется indexing_service вместе с document_text.
    subject_snapshot: Mapped[str | None] = mapped_column(Text)
    # Сырой извлечённый текст всех документов закупки (конкатенация, scoring_common.tz).
    document_text: Mapped[str | None] = mapped_column(Text)
    # GENERATED ALWAYS AS ... STORED (Computed — та же DDL, что и в Liquibase-миграции,
    # чтобы Base.metadata.create_all в интеграционных тестах давал идентичную схему).
    # Конфигурация 'simple' (НЕ 'russian'!) — намеренно: DSL ключевых слов
    # (parser/filtering.py) не делает лингвистическую нормализацию (стемминг по
    # словарю), а только СИМВОЛЬНОЕ усечение (`слов*` -> префикс) или точное слово.
    # 'russian' применил бы морфологический стемминг Postgres и разошёлся бы с
    # семантикой live-пути; 'simple' (нижний регистр + токенизация без стемминга)
    # даёт параметрам транслятора (parser/filtering_tsquery.py) точное соответствие.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(subject_snapshot, '') || ' ' || "
            "coalesce(document_text, ''))",
            persisted=True,
        ),
    )
    # Хэш содержания источников (subject + состав/содержимое файлов) — идемпотентное
    # переиндексирование: не тянуть документы повторно, если ничего не изменилось.
    content_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    procurement_rel: Mapped[Procurement] = relationship(back_populates="search_index_rel")
