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
    DateTime,
    ForeignKey,
    Index,
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
    """Индекс одной закупки: tsvector для полнотекстового поиска (без сырого текста).

    Один-к-одному с ``procurements`` (``UniqueConstraint`` на ``procurement_id``).
    ``search_tsv`` — обычная колонка (НЕ ``GENERATED``): пишется явно из
    ``SearchIndexMixin.save_index_result`` через ``func.to_tsvector('simple', ...)``
    в момент успешной индексации (одна и та же postgres-функция, что и раньше
    использовалась в ``GENERATED``-выражении — механизм токенизации не меняется,
    поэтому tsquery-транслятор (``parser/filtering_tsquery.py``) остаётся точным
    парным соответствием). Раньше колонка была ``GENERATED ALWAYS AS ... STORED``
    из ``subject_snapshot``+``document_text`` (сырой конкатенированный текст всех
    документов закупки), но ``document_text`` нигде не читался, кроме этого
    выражения (подтверждено grep по всему репозиторию) — хранить его было
    избыточно (TOAST), поэтому колонка убрана (db.changelog-1.58.yaml): текст
    по-прежнему извлекается ``indexing_service`` и приходит по HTTP как раньше,
    просто не персистится — только его tsvector.
    ``status`` — жизненный цикл индексации одной закупки: ``pending`` (запись
    создана, файлы ещё не обработаны) -> ``indexed`` (``search_tsv`` актуален) |
    ``error`` (сбой скачивания/извлечения — ``error_message``, повтор на
    следующей итерации; предыдущий успешный ``search_tsv`` при этом сохраняется).

    Индексы объявлены и здесь, и в Liquibase-миграции (db.changelog-1.57.yaml) —
    та же DDL продублирована намеренно: инцидент (2026-09) показал, что
    ``Base.metadata.drop_all/create_all`` (интеграционные тесты; по ошибке был
    применён и к реальной БД) пересоздаёт схему ТОЛЬКО из этой модели — Liquibase
    в такой ситуации считает changeset уже применённым и не переисполняет его,
    так что не объявленные здесь индексы (в т.ч. GIN на ``search_tsv`` — без него
    полнотекстовый поиск скатывается на Seq Scan) молча пропадают.
    """

    __tablename__ = "procurement_search_index"
    __table_args__ = (
        UniqueConstraint("procurement_id", name="uq_procurement_search_index_procurement"),
        Index("ix_procurement_search_index_procurement", "procurement_id"),
        Index("ix_procurement_search_index_okpd2", "okpd2_normalized"),
        Index(
            "ix_procurement_search_index_search_tsv",
            "search_tsv",
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    procurement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("procurements.id", ondelete="CASCADE"), nullable=False
    )
    # Нормализованный код ОКПД2 закупки (см. okpd.py::normalize_okpd_codes) —
    # денормализация для быстрой фильтрации по диапазону без похода в procurements.
    okpd2_normalized: Mapped[str | None] = mapped_column(Text)
    # Снимок subject закупки на момент последней успешной индексации (Procurement.subject
    # на тот момент) — используется только как вход для search_tsv ниже, отдельно нигде
    # не читается. Пишется вместе с search_tsv в save_index_result.
    subject_snapshot: Mapped[str | None] = mapped_column(Text)
    # Полнотекстовый индекс: пишется явно (см. докстринг класса) через
    # func.to_tsvector('simple', subject_snapshot + ' ' + document_text) в момент
    # успешной индексации — сырой document_text при этом не сохраняется, только
    # результат его токенизации. Конфигурация 'simple' (НЕ 'russian'!) — намеренно:
    # DSL ключевых слов (parser/filtering.py) не делает лингвистическую нормализацию
    # (стемминг по словарю), а только СИМВОЛЬНОЕ усечение (`слов*` -> префикс) или
    # точное слово. 'russian' применил бы морфологический стемминг Postgres и
    # разошёлся бы с семантикой live-пути; 'simple' (нижний регистр + токенизация
    # без стемминга) даёт параметрам транслятора (parser/filtering_tsquery.py)
    # точное соответствие.
    search_tsv: Mapped[str | None] = mapped_column(TSVECTOR)
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
