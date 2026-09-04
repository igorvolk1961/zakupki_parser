"""Профили фильтрации пользователей и связанные справочники (лицензии, опыт)."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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

if TYPE_CHECKING:
    from zakupki_parser.storage.db.user import User


class Profile(Base):
    """Профиль фильтрации пользователя (тендеролога).

    Поля: компетенции (текст для LLM-скоринга), ключевые слова и слова-исключения
    для предварительной фильтрации, regex-паттерны контекста ключевых слов,
    вопросы к ТЗ для RAG-анализа стоп-условий (``{id, text}``), целевые ЭТП и законы,
    порог Fit. Принадлежит ``user_id`` (BR-07); ``is_active`` — выбранный пользователем
    активный профиль (per-user состояние).
    """

    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_profiles_user_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    min_fit_threshold: Mapped[float | None] = mapped_column(Float)
    # target_etp/target_laws/min_fit_threshold зарезервированы (CRUD в API, влияние
    # на парсинг/пороги — пост-MVP, этапы 4/5).
    target_etp: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_laws: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Целевые регионы профиля (клиентская пост-фильтрация, как ключевые слова R9).
    target_regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Максимально допустимое расстояние от центра целевого региона, км. Проверяется
    # ТОЛЬКО на этапе анализа (внешний сервис): при заданном расстоянии парсер НЕ
    # отсекает закупку по строковому региону (решение требует гео-координат).
    max_region_distance_km: Mapped[float | None] = mapped_column(Float)
    # Критерии поиска принадлежат ПРОФИЛЮ (не глобальному конфигу): коды ОКПД2
    # и диапазон НМЦК. Выбор по состоянию (active_only) — глобальный
    # config_service.yaml -> search_criteria.active_only. Используются парсером при обходе ЭТП.
    okpd_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    nmck_min: Mapped[float | None] = mapped_column(Float)
    nmck_max: Mapped[float | None] = mapped_column(Float)
    competencies: Mapped[str] = mapped_column(Text, nullable=False)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user_rel: Mapped[User | None] = relationship(back_populates="profiles")
    # Ключевые слова и слова-исключения профиля хранятся ТОЛЬКО в таблице ``keywords``
    # (отдельная сущность, BR-07/ER): JSONB-массивов keywords/exclusion_words в профиле нет.
    keywords_rel: Mapped[list[Keyword]] = relationship(
        order_by="Keyword.type, Keyword.id", cascade="all, delete-orphan"
    )
    # Лицензии компании-заказчика работы тендеролога и подтверждённый опыт (BR-03).
    licenses: Mapped[list[ProfileLicense]] = relationship(
        back_populates="profile_rel", cascade="all, delete-orphan"
    )
    experience: Mapped[list[ProfileExperience]] = relationship(
        back_populates="profile_rel", cascade="all, delete-orphan"
    )


class Keyword(Base):
    """Нормализованные ключевые слова профиля (канонический источник).

    ``type`` — ``keyword`` (позитивное) или ``exclusion`` (слово-исключение).
    Слова НЕ дублируются в JSONB-полях профиля (ER: PROFILE -> KEYWORD); рабочий
    набор парсера/фильтрации читается из этой таблицы (см. ``Profile.keywords_rel``).
    Сид для профиля — скрипт ``zp seed-profile`` из файла-сида профиля (R8).
    """

    __tablename__ = "keywords"
    __table_args__ = (
        UniqueConstraint("profile_id", "word", "type", name="uq_keywords_profile_word_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    word: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'keyword'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExperienceConfirmationType(Base):
    """Справочник типов подтверждения опыта (сид — BR-03, миграция 1.37).

    ``code`` — стабильный ключ: ``platform`` (через электронную площадку, ПП РФ 2571),
    ``documents`` (сканы договоров/актов), ``registry`` (выписка из реестра контрактов).
    Справочник глобальный (не привязан к профилю/пользователю); заполняется при
    миграции и идемпотентно ``ensure_reference_data`` на старте приложения.
    """

    __tablename__ = "experience_confirmation_types"
    __table_args__ = (UniqueConstraint("code", name="uq_experience_confirmation_types_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    experiences: Mapped[list[ProfileExperience]] = relationship(
        back_populates="confirmation_type_rel"
    )


class LicenseType(Base):
    """Справочник видов лицензий/допусков (сид — docs/references/licenze_kind.md).

    Вид лицензии идентифицируется ``name`` (уникальный, case-sensitive индекс):
    каталожные наименования, например «деятельность по тушению пожаров в населённых
    пунктах…». Поле ``code`` удалено (миграция 1.50): код больше не идентифицирует вид.
    Справочник глобальный (не привязан к профилю/пользователю); заполняется при
    миграции и идемпотентно ``ensure_reference_data`` на старте приложения.
    """

    __tablename__ = "license_types"
    __table_args__ = (UniqueConstraint("name", name="uq_license_types_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    licenses: Mapped[list[ProfileLicense]] = relationship(back_populates="license_type_rel")


class ProfileLicense(Base):
    """Лицензия компании-заказчика работы тендеролога (профиль, BR-07).

    Лицензия идентифицируется типом (``license_type_id`` — справочник
    ``license_types``), номером и органом, выдавшим лицензию. ``expiry_date`` NULL —
    бессрочная лицензия. Статус (активна/истекла) вычисляется на стороне клиента
    по ``expiry_date`` и текущей дате.
    """

    __tablename__ = "profile_licenses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    license_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("license_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    number: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[str | None] = mapped_column(Text)
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profile_rel: Mapped[Profile] = relationship(back_populates="licenses")
    license_type_rel: Mapped[LicenseType] = relationship(back_populates="licenses")


class ProfileExperience(Base):
    """Подтверждённый опыт компании-заказчика работы тендеролога (профиль, BR-07).

    ``confirmation_type_id`` — справочник ``experience_confirmation_types`` (сид BR-03):
    подтверждение через площадку (ПП РФ 2571), сканы договоров/актов, выписка из
    реестра контрактов. ``import_independent`` — соответствие требованию Минпромторга
    об импортонезависимости: true — соответствует, false — не соответствует,
    NULL — неизвестно/не применимо.
    """

    __tablename__ = "profile_experience"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(Text)
    contract_number: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[float | None] = mapped_column(Float)
    confirmation_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("experience_confirmation_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    import_independent: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profile_rel: Mapped[Profile] = relationship(back_populates="experience")
    confirmation_type_rel: Mapped[ExperienceConfirmationType] = relationship(
        back_populates="experiences"
    )
