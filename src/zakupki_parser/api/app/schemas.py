"""Pydantic-схемы API: ответы закупок/заказчиков/профилей и запросы.

Выделено из прежнего монолитного ``api/app.py`` (раздел «Схемы ответов»).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zakupki_parser.config.models import SCORE_METHOD_FIT, SCORE_METHOD_STAGES


class ProcurementOut(BaseModel):
    """Карточка закупки (без detail_json)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    platform_id: str
    platform_name: str | None = None
    platform_url: str | None = None
    url: str | None = None
    customer_id: int | None = None
    customer: str | None = None
    procedure_type_id: int | None = None
    procedure_type: str | None = None
    law: str | None = None
    subject: str | None = None
    nmck: float | None = None
    publication_date: datetime | None = None
    update_date: datetime | None = None
    deadline: datetime | None = None
    execution_term: str | None = None
    okpd2_codes: str | None = None
    kpgz_codes: str | None = None
    security_amount: float | None = None
    security_amount_unit: str | None = None
    files_json: list[dict[str, Any]] | None = None
    score: float | None = None
    fit_score: float | None = None
    p_win: float | None = None
    margin: float | None = None
    score_method: str | None = None
    embedding_similarity: float | None = None
    # Per-client RAG-отчёт анализа стоп-условий (профиль активного клиента).
    rag_report: dict[str, Any] | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ProcurementDetailOut(ProcurementOut):
    """Карточка закупки с полным detail_json."""

    detail_json: dict[str, Any] | None = None


class ProcurementListOut(BaseModel):
    total: int
    items: list[ProcurementOut]


class CustomerOut(BaseModel):
    """Карточка заказчика (ADR-4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    inn: str | None = None
    rating: float | None = None
    created_at: datetime
    updated_at: datetime


class CustomerListOut(BaseModel):
    total: int
    items: list[CustomerOut]


class RatingUpdate(BaseModel):
    """Установка рейтинга заказчика внешним сервисом (ADR-4)."""

    rating: float


class ClearIrrelevantIn(BaseModel):
    """Удаление нерелевантных закупок: порог релевантности (fit_score)."""

    min_fit_score: float = 0.4


class ExportIn(BaseModel):
    """Выгрузка CSV: порог релевантности (fit_score), как в фильтре таблицы."""

    min_fit_score: float = 0.4


class HealthOut(BaseModel):
    status: str
    db: bool


class LoginIn(BaseModel):
    """Вход по логину и паролю (пока; позже — OAuth2 через Сбер ID)."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    """Карточка пользователя (без password_hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None = None
    role: str
    created_at: datetime
    updated_at: datetime


class RegisterIn(BaseModel):
    """Самостоятельная регистрация: пользователь сам выбирает пароль.

    Требуется подтверждение пароля (``password_confirm``). Роль при регистрации
    всегда ``tenderologist``; роль администратора регистрацией не выдаётся.
    """

    username: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8)
    password_confirm: str = Field(min_length=8)

    @model_validator(mode="after")
    def _passwords_match(self) -> RegisterIn:
        if self.password != self.password_confirm:
            raise ValueError("password_confirm не совпадает с password")
        return self


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class PromptUpdate(BaseModel):
    """Сохранение промпта scoring_service (вкладка «Промпты»)."""

    content: str


class ScoreUpdate(BaseModel):
    """Обновление score внешним сервисом (по его инициативе)."""

    score: float
    fit_score: float | None = None
    p_win: float | None = None
    margin: float | None = None
    score_method: str = SCORE_METHOD_FIT
    embedding_similarity: float | None = None
    rag_report: dict[str, Any] | None = None

    @field_validator("score_method")
    @classmethod
    def _score_method_known(cls, value: str) -> str:
        """Принимаем только известные результаты внешнего скоринга (ADR-7/ADR-8).

        Неизвестный метод (например, будущая стадия каскада, о которой парсер ещё
        не знает) раньше молча сохранялся в БД и «терялся» для фильтров и таблицы —
        теперь такой запрос отклоняется с 422.
        """
        if value not in SCORE_METHOD_STAGES:
            allowed = ", ".join(SCORE_METHOD_STAGES)
            raise ValueError(f"score_method должен быть одним из: {allowed}")
        return value


class ProcurementIdsIn(BaseModel):
    """Пакетная обработка выбранных закупок (on-demand)."""

    procurement_ids: list[int] = Field(min_length=1)


class LicenseIn(BaseModel):
    """Создание/обновление лицензии компании (профиль).

    Лицензия идентифицируется типом (``license_type_id``) и номером; ``expiry_date``
    NULL — бессрочная лицензия.
    """

    license_type_id: int
    number: str | None = Field(default=None, max_length=128)
    authority: str | None = Field(default=None, max_length=256)
    issue_date: date | None = None
    expiry_date: date | None = None
    notes: str | None = None


class ExperienceIn(BaseModel):
    """Создание/обновление записи подтверждённого опыта (профиль).

    ``confirmation_type_id`` — справочник ``experience_confirmation_types`` (BR-03);
    ``import_independent`` — соответствие требованию Минпромторга об импортонезависимости
    (true/false, NULL — неизвестно/не применимо).
    """

    title: str = Field(min_length=1, max_length=512)
    customer_name: str | None = Field(default=None, max_length=256)
    contract_number: str | None = Field(default=None, max_length=128)
    start_date: date | None = None
    end_date: date | None = None
    amount: float | None = None
    confirmation_type_id: int
    import_independent: bool | None = None
    notes: str | None = None


class ProfileFactsOut(BaseModel):
    """Факты профиля для Stage B анализа ТЗ (лицензии, подтверждённый опыт).

    Лёгкий срез для сопоставления с фактами ТЗ; профиль в промпт RAG не попадает.
    """

    license_codes: list[str]
    experience_codes: list[str]


class ProfileIn(BaseModel):
    """Создание/обновление профиля фильтрации пользователя (ключ — user_id + name).

    ``licenses``/``experience`` — записи профиля (BR-03), сохраняются вместе с
    профилем полной заменой: веб-редактор держит их в форме до «Сохранить профиль».
    """

    name: str = Field(min_length=1, max_length=128)
    enabled: bool | None = None
    is_active: bool | None = None
    competencies: str | None = None
    keywords: list[str] | None = None
    exclusion_words: list[str] | None = None
    questions: list[dict[str, Any]] | None = None
    target_etp: list[str] | None = None
    target_laws: list[str] | None = None
    min_fit_threshold: float | None = None
    okpd_codes: list[str] | None = None
    nmck_min: float | None = None
    nmck_max: float | None = None
    licenses: list[LicenseIn] | None = None
    experience: list[ExperienceIn] | None = None


class ProfileOut(BaseModel):
    """Карточка профиля фильтрации."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    enabled: bool
    is_active: bool
    competencies: str
    # Слова профиля живут в таблице keywords (канонический источник) и подставляются
    # в _profile_out после валидации модели — дефолт нужен, чтобы model_validate(profile)
    # не падал на отсутствующих атрибутах ORM-объекта.
    keywords: list[str] = Field(default_factory=list)
    exclusion_words: list[str] = Field(default_factory=list)
    questions: list[dict[str, Any]]
    target_etp: list[str]
    target_laws: list[str]
    min_fit_threshold: float | None = None
    okpd_codes: list[str]
    nmck_min: float | None = None
    nmck_max: float | None = None
    created_at: datetime
    updated_at: datetime
    # Факты профиля для сопоставления с фактами ТЗ (только в active_client).
    facts: ProfileFactsOut | None = None


class ProfileListOut(BaseModel):
    total: int
    items: list[ProfileOut]


class LicenseTypeOut(BaseModel):
    """Справочник типов лицензий (сид — набор для ИТ-компании)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class ConfirmationTypeOut(BaseModel):
    """Справочник типов подтверждения опыта (сид BR-03)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class LicenseOut(BaseModel):
    """Карточка лицензии компании."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    license_type_id: int
    license_type: LicenseTypeOut | None = None
    number: str | None = None
    authority: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class LicenseListOut(BaseModel):
    total: int
    items: list[LicenseOut]


class ExperienceOut(BaseModel):
    """Карточка подтверждённого опыта компании."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    title: str
    customer_name: str | None = None
    contract_number: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    amount: float | None = None
    confirmation_type_id: int
    confirmation_type: ConfirmationTypeOut | None = None
    import_independent: bool | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class ExperienceListOut(BaseModel):
    total: int
    items: list[ExperienceOut]
