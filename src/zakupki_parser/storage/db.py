"""SQLAlchemy 2.x модели и работа с БД (PostgreSQL)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

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
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from zakupki_parser.config.models import DbConfig


class Base(DeclarativeBase):
    # Разрешаем немэпленные аннотированные атрибуты на моделях (например
    # Procurement.rag_report — per-client RAG-отчёт, колонки в procurements нет).
    __allow_unmapped__ = True


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
    """

    __tablename__ = "platforms"
    __table_args__ = (UniqueConstraint("platform_id", name="uq_platforms_platform_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    """Пользователь сервиса: администратор или тендеролог.

    Роли: ``admin`` — управление сервисом (парсер, конфиги, пользователи, очистка БД);
    ``tenderologist`` — работа с закупками (просмотр, анализ). Каждый пользователь —
    отдельный tenant (BR-07): профили фильтрации и оценки принадлежат ``user_id``.
    Пока вход по логину/паролю (пароль — PBKDF2-хэш, см. ``zakupki_parser.auth``);
    позже — OAuth2 через Сбер ID.
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profiles: Mapped[list[Profile]] = relationship(back_populates="user_rel")


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
    Сид для профиля — скрипт ``zp seed-profile`` из ``docs/references/profile.md`` (R8).
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
    """Справочник типов лицензий (сид — набор для ИТ-компании, миграция 1.37).

    ``code`` — стабильный ключ: ``fstek``, ``fsb``, ``mincifry``, ``roscomnadzor``,
    ``minpromtorg``, ``mchs``, ``rosgvardia``, ``education``, ``other``.
    Справочник глобальный (не привязан к профилю/пользователю); заполняется при
    миграции и идемпотентно ``ensure_reference_data`` на старте приложения.
    """

    __tablename__ = "license_types"
    __table_args__ = (UniqueConstraint("code", name="uq_license_types_code"),)

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
    rag_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Ключевые слова профиля, по которым закупка прошла клиентскую фильтрацию (R9).
    matched_keywords: Mapped[list[str] | None] = mapped_column(JSONB)
    # status/rejection_reason зарезервированы под Эпик 5 («В работу»/«Отклонить») —
    # пост-MVP (этап 7); сейчас всегда status='new', rejection_reason=NULL.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    procurement_rel: Mapped[Procurement] = relationship(back_populates="evaluations")


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
    # Отметка успешной постановки закупки в очередь внешнего скоринга (fit).
    # NULL — задача не поставлена (в т.ч. транспорт был недоступен при сохранении);
    # recovery по ней догоняет пропущенные закупки (см. repository.find_unscored).
    scoring_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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
    rag_report: dict[str, Any] | None = None

    customer_rel: Mapped[Customer | None] = relationship(back_populates="procurements")
    procedure_type_rel: Mapped[ProcedureType | None] = relationship(back_populates="procurements")
    platform_rel: Mapped[Platform | None] = relationship(
        primaryjoin="Procurement.platform_id == foreign(Platform.platform_id)",
        viewonly=True,
    )
    evaluations: Mapped[list[ProcurementEvaluation]] = relationship(
        back_populates="procurement_rel", cascade="all, delete-orphan"
    )


class Database:
    """Тонкая обёртка над SQLAlchemy async engine/session."""

    def __init__(self, cfg: DbConfig) -> None:
        self._cfg = cfg
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._cfg.dsn,
            pool_size=self._cfg.pool_max,
            max_overflow=0,
            pool_pre_ping=True,
            connect_args={
                "timeout": self._cfg.connect_timeout_seconds,
            },
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        if self._session_factory is None:
            raise RuntimeError("БД не подключена")
        return self._session_factory()

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    @property
    def is_connected(self) -> bool:
        return self._engine is not None
