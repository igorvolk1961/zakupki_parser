"""Per-profile результаты скоринга закупок (fit/pwin/margin/rag_report)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zakupki_parser.storage.db.base import Base

if TYPE_CHECKING:
    from zakupki_parser.storage.db.procurement import Procurement


class ProcurementEvaluation(Base):
    """Per-profile результат скоринга закупки (fit/pwin/margin/rag_report).

    Ключ ``(procurement_id, profile_id)`` (BR-07): одна закупка оценивается под
    каждый профиль фильтрации (контекст компетенций/вопросов принадлежит профилю;
    профиль — пользователю). Результаты формируются автоматически (auto-Fit);
    ручная корректировка — вне MVP (этап 7).
    """

    __tablename__ = "procurement_evaluations"
    __table_args__ = (
        UniqueConstraint("procurement_id", "profile_id", name="uq_evaluations_proc_profile"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    procurement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("procurements.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE")
    )
    fit_score: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    p_win: Mapped[float | None] = mapped_column(Float)
    margin: Mapped[float | None] = mapped_column(Float)
    score_method: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    # Векторная близость терминальной отсечки (score_method=sim, ADR-8).
    embedding_similarity: Mapped[float | None] = mapped_column(Float)
    # Глубокая ссылка на LangFuse-трейс скоринга закупки (строится scoring_service
    # по trace_id; None, если LangFuse не настроен/недоступен — кнопка «Трейс»
    # на карточке не отображается).
    langfuse_trace_url: Mapped[str | None] = mapped_column(Text)
    rag_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Ключевые слова профиля, по которым закупка прошла клиентскую фильтрацию (R9).
    matched_keywords: Mapped[list[str] | None] = mapped_column(JSONB)
    # Метка успешной постановки задания в очередь внешнего скоринга — по профилю
    # (пер-профильно, BR-07): recovery догоняет (закупка, профиль), которые не
    # попали в очередь, а не только закупку целиком.
    scoring_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Хэш канонического содержания компетенций профиля (BR-07): ключ дедупликации
    # скоринга — профили с идентичным содержанием компетенций обрабатываются один
    # раз, результат пишется всем подписанным профилям этой группы.
    comp_hash: Mapped[str | None] = mapped_column(Text)
    # status/rejection_reason зарезервированы под Эпик 5 («В работу»/«Отклонить») —
    # пост-MVP (этап 7); сейчас всегда status='new', rejection_reason=NULL.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    procurement_rel: Mapped[Procurement] = relationship(back_populates="evaluations")
