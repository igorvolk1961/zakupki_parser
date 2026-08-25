"""Базовый класс SQLAlchemy для всех моделей (storage/db)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # Разрешаем немэпленные аннотированные атрибуты на моделях (например
    # Procurement.rag_report — per-client RAG-отчёт, колонки в procurements нет).
    __allow_unmapped__ = True
