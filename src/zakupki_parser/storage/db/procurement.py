"""Модели закупок и справочники процедур/платформ/категорий."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zakupki_parser.storage.db.base import Base
from zakupki_parser.storage.db.evaluation import ProcurementEvaluation

if TYPE_CHECKING:
    from zakupki_parser.storage.db.customer import Customer


class ProcedureType(Base):
    """Справочник типов процедур (покупок).

    ``is_canonical=true`` — канонический тип из предзагруженного справочника
    (способы 44-ФЗ/223-ФЗ); ``false`` — «сырое» значение площадки без маппинга
    (накоплено find-or-create при отсутствии строки в ``ProcedureTypeMapping``).
    """

    __tablename__ = "procedure_types"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_procedure_types_normalized_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_canonical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    procurements: Mapped[list[Procurement]] = relationship(back_populates="procedure_type_rel")


class ProcedureTypeMapping(Base):
    """Соответствие «площадка + родное значение» -> канонический тип процедуры.

    Предзагруженный справочник: по нему ``purchase_type`` площадки резолвится в
    канонический ``procedure_types.id`` (например «Электронный запрос котировок»
    roseltorg -> «Запрос котировок»).
    """

    __tablename__ = "procedure_type_mappings"
    __table_args__ = (
        UniqueConstraint("platform_id", "normalized_name", name="uq_procedure_type_mapping"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_id: Mapped[str] = mapped_column(String(128), nullable=False)
    native_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    procedure_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("procedure_types.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Platform(Base):
    """Справочник платформ: ключ, официальное наименование, главная страница.

    ``platform_id`` — натуральный ключ, совпадает с ``configs/dom/<platform_id>.yaml``
    и ``procurements.platform_id``. Справочник для отображения/join'ов по ключу
    (FK на procurements не ставится: ключ стабильный, а набор платформ — конфиг).
    ``enabled`` — активность площадки (источник истины — БД); конфиг
    (config_service.yaml -> sites) — редактируемый интерфейс, синхронизируется
    в БД при старте приложения и сохранении конфига.
    """

    __tablename__ = "platforms"
    __table_args__ = (UniqueConstraint("platform_id", name="uq_platforms_platform_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProcedureCategory(Base):
    """Категория закупки по ОКПД2 (заглушка; ``pwin_coefficient`` не используется)."""

    __tablename__ = "procedure_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    pwin_coefficient: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Procurement(Base):
    """Запись о закупке (публичные данные ЭТП).

    Оценок (score/fit_score/p_win/margin/score_method) здесь НЕТ: результаты
    скоринга живут только в ``procurement_evaluations`` (per-profile). Динамические
    атрибуты ``score``/``fit_score``/``p_win``/``margin``/``score_method``/
    ``rag_report`` подкладываются репозиторием при выдаче (``_apply_profile_score``).
    """

    __tablename__ = "procurements"
    __table_args__ = (
        UniqueConstraint("number", "platform_id", name="uq_procurement_number_platform"),
    )
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    platform_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024))
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="SET NULL")
    )
    procedure_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("procedure_types.id", ondelete="SET NULL")
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("procedure_categories.id", ondelete="SET NULL")
    )
    law: Mapped[str | None] = mapped_column(String(16))
    subject: Mapped[str | None] = mapped_column(Text)
    nmck: Mapped[float | None] = mapped_column(Float)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    update_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_term: Mapped[str | None] = mapped_column(Text)
    security_amount: Mapped[float | None] = mapped_column(Float)
    security_amount_unit: Mapped[str | None] = mapped_column(String(16))
    advance: Mapped[float | None] = mapped_column(Float)
    okpd2_codes: Mapped[str | None] = mapped_column(Text)
    kpgz_codes: Mapped[str | None] = mapped_column(Text)
    files_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Контекст досборки деталей ПОСЛЕ скоринга (BR-08): api_fields (need_id и т.п.),
    # сохранённые при персисте на уровне списка. NULL — детали дособраны/не требуются.
    detail_api: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Метка успешной досборки деталей площадки (BR-08): NULL — досборка не выполнена.
    details_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Номер итерации цикла планировщика, в которой закупка поставлена в очередь
    # скоринга (батч для журнала метрик «Метрики»). NULL — метрика не записана
    # (старые данные / постановка вне цикла парсера).
    scoring_iteration: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Динамические атрибуты скоринга (НЕ колонки, __allow_unmapped__): подкладываются
    # репозиторием (_apply_profile_score) при выдаче под активный профиль из
    # procurement_evaluations. В БД оценок в procurements нет.
    score: float | None = None
    fit_score: float | None = None
    p_win: float | None = None
    margin: float | None = None
    score_method: str | None = None
    embedding_similarity: float | None = None
    langfuse_trace_url: str | None = None
    rag_report: dict[str, Any] | None = None
    costs: dict[str, Any] | None = None

    customer_rel: Mapped[Customer | None] = relationship(back_populates="procurements")
    procedure_type_rel: Mapped[ProcedureType | None] = relationship(back_populates="procurements")
    platform_rel: Mapped[Platform | None] = relationship(
        primaryjoin="Procurement.platform_id == foreign(Platform.platform_id)",
        viewonly=True,
    )
    evaluations: Mapped[list[ProcurementEvaluation]] = relationship(
        back_populates="procurement_rel", cascade="all, delete-orphan"
    )
