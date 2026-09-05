"""Pydantic-схемы API: ответы закупок/заказчиков/профилей и запросы.

Выделено из прежнего монолитного ``api/app.py`` (раздел «Схемы ответов»).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zakupki_parser.config.models import SCORE_METHOD_FIT, SCORE_METHOD_STAGES
from zakupki_parser.okpd import normalize_okpd_codes
from zakupki_parser.options import PAID_KEYS, option_by_key


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
    region: str | None = None
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
    # Глубокая ссылка на LangFuse-трейс скоринга (кнопка «Трейс» на карточке).
    langfuse_trace_url: str | None = None
    # Per-client RAG-отчёт анализа стоп-условий (профиль активного клиента).
    rag_report: dict[str, Any] | None = None
    # Стоимость обработки закупки (USD) по этапам: {"scoring": {"usd": ...},
    # "analysis": {"usd": ...}}. Отдаётся только роли analyst (см. converters).
    costs: dict[str, Any] | None = None
    # Закупка принята «в работу» активным профилем (Эпик 5, US-5.4–5.6).
    in_work: bool = False
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ProcurementDetailOut(ProcurementOut):
    """Карточка закупки с полным detail_json."""

    detail_json: dict[str, Any] | None = None
    # Требования к участнику (поиск по всем документам, не только ТЗ): структура
    # {licenses, experience, minprom, other}, где каждый тип — список объектов
    # {text, data, file_name} (+ optional additional для таблиц). Нужна analysis-воркеру
    # (get_procurement), чтобы понять, извлечены ли требования и какие data заполнены.
    requirements_json: dict[str, Any] | None = None


class RequirementsOut(BaseModel):
    """Структура «Требования к участнику» закупки (просмотр в карточке).

    ``found`` — False, если требования не найдены (структура пустая ``{}``) либо
    поле ещё не извлекалось и извлечение тоже ничего не нашло.
    """

    found: bool
    requirements: dict[str, Any] | None = None


class RequirementsIn(BaseModel):
    """Сохранение структуры требований (analysis-воркер / извлечение)."""

    structure: dict[str, Any]


class ProcurementListOut(BaseModel):
    total: int
    items: list[ProcurementOut]


class WorkItemOut(BaseModel):
    """Запись «в работе» профиля (закупка, принятая тендерологом).

    ``procurement_id`` NULL — закупка удалена из общей базы либо ещё не сохранена
    парсером (принята по URL): карточка отдаётся из снимка полей в записи.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    procurement_id: int | None = None
    source: str
    status: str
    notes: str | None = None
    accepted_at: datetime
    # Снимок карточки закупки на момент принятия (resilience к удалению закупки).
    number: str | None = None
    platform_id: str | None = None
    url: str | None = None
    subject: str | None = None
    nmck: float | None = None
    deadline: datetime | None = None
    law: str | None = None
    customer_name: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkItemsListOut(BaseModel):
    total: int
    items: list[WorkItemOut]


class RejectIn(BaseModel):
    """Отбраковка закупки профилем (Эпик 5, US-5.1/5.2).

    ``remove_matched_keywords`` — убрать из профиля ключевые фразы, по которым
    закупка была отобрана (matched_keywords). ``exclusion_word`` — добавить
    слово-исключение в профиль (явное действие пользователя; предложение
    «без автоприменения», US-5.3, отложено).
    """

    rejection_reason: str | None = None
    remove_matched_keywords: bool = False
    exclusion_word: str | None = Field(default=None, max_length=256)


class AcceptWorkIn(BaseModel):
    """Принятие закупки «в работу»: необязательная заметка."""

    notes: str | None = None


class AcceptWorkByUrlIn(BaseModel):
    """Принятие «в работу» по URL закупки на ЭТП (не из результатов поиска)."""

    url: str = Field(min_length=1, max_length=1024)
    notes: str | None = None


class ClearDbIn(BaseModel):
    """Очистка БД (devops): удалять ли закупки, принятые «в работу».

    По умолчанию False — записи «в работе» сохраняются (procurement_id обнуляется,
    карточка читается из снимка), даже если результаты поиска удалены.
    """

    include_work_items: bool = False


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


class PlatformOut(BaseModel):
    """Площадка из справочника ``platforms`` + активность (config_service.yaml)."""

    platform_id: str
    name: str
    url: str
    enabled: bool


class PlatformsListOut(BaseModel):
    items: list[PlatformOut]


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
    roles: list[str]
    status: str = "active"
    # Окончание триал-режима (null — нет триала). Дата серверная: клиент не
    # должен полагаться на собственные часы для расчёта оставшихся дней.
    trial_end_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# Роли, которые администратор может выдавать/снимать (роль «user» — только
# через саморегистрацию и админом не управляется).
ADMIN_ASSIGNABLE_ROLES = ("admin", "analyst", "devops")


def _validate_assignable_roles(roles: list[str]) -> list[str]:
    """Проверяет, что роли непусты и состоят только из выдаваемых админом ролей."""
    unknown = set(roles) - set(ADMIN_ASSIGNABLE_ROLES)
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise ValueError(f"Роль 'user' и неизвестные роли выдавать нельзя: {joined}")
    if not roles:
        raise ValueError("Укажите хотя бы одну роль")
    return roles


class UserIn(BaseModel):
    """Создание пользователя администратором (роли {admin, analyst, devops})."""

    username: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8)
    roles: list[str]

    @field_validator("roles")
    @classmethod
    def _roles_assignable(cls, value: list[str]) -> list[str]:
        return _validate_assignable_roles(value)


class UserRolesIn(BaseModel):
    """Смена ролей пользователя администратором (без роли «user»)."""

    roles: list[str]

    @field_validator("roles")
    @classmethod
    def _roles_assignable(cls, value: list[str]) -> list[str]:
        return _validate_assignable_roles(value)


class UserStatusIn(BaseModel):
    """Блокировка/разблокировка аккаунта."""

    status: Literal["active", "blocked"]


class UsersListOut(BaseModel):
    total: int
    items: list[UserOut]


class RegisterIn(BaseModel):
    """Самостоятельная регистрация: пользователь сам выбирает пароль.

    Требуется подтверждение пароля (``password_confirm``). Роль при регистрации
    всегда ``user``; роли admin/analyst/devops регистрацией не выдаются.
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
    profile_id: int = Field(
        description="профиль, для которого посчитан результат (пер-профильно, BR-07)"
    )
    fit_score: float | None = None
    p_win: float | None = None
    margin: float | None = None
    score_method: str = SCORE_METHOD_FIT
    embedding_similarity: float | None = None
    langfuse_trace_url: str | None = None
    rag_report: dict[str, Any] | None = None
    score_costs: dict[str, Any] | None = None

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
    """Факты профиля для Stage B анализа ТЗ (виды лицензий, подтверждённый опыт).

    Лёгкий срез для сопоставления с фактами ТЗ; профиль в промпт RAG не попадает.
    Виды лицензий — наименования (``license_types.name``), а не коды.
    """

    license_names: list[str]
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
    target_regions: list[str] | None = None
    max_region_distance_km: float | None = None
    min_fit_threshold: float | None = None
    okpd_codes: list[str] | None = None
    nmck_min: float | None = None
    nmck_max: float | None = None
    licenses: list[LicenseIn] | None = None
    experience: list[ExperienceIn] | None = None

    @field_validator("okpd_codes")
    @classmethod
    def _okpd_format(cls, value: list[str] | None) -> list[str] | None:
        """Контроль формата кодов ОКПД2 (#1): цифры с точками, 2-9 знаков."""
        return normalize_okpd_codes(value)


class ProfileImportIn(BaseModel):
    """Загрузка профиля из файла (разметка как у файла-сида профиля)."""

    content: str


class ProfileExportOut(BaseModel):
    """Экспорт профиля единым JSON-файлом (компетенции — подобъект внутри).

    ``profile_content`` — полный JSON профиля: поля ``profile`` (name, okpd_codes,
    nmck_min/max, keywords, exclusion_words, questions, …) и ``competencies``
    (подобъект компетенций). Файл самодостаточен: его можно повторно загрузить
    через ``/api/clients/import`` без внешних ссылок.
    """

    profile_filename: str
    profile_content: str


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
    target_regions: list[str] = Field(default_factory=list)
    max_region_distance_km: float | None = None
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
    """Справочник видов лицензий (сид — docs/references/licenze_kind.md)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
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


class LicenseTypeIn(BaseModel):
    """Создание/обновление вида лицензии (админ-страница «Справочники»)."""

    name: str = Field(min_length=1, max_length=512)
    sort_order: int = 0


class ConfirmationTypeIn(BaseModel):
    """Создание/обновление типа подтверждения опыта (админ-страница «Справочники»)."""

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=256)
    sort_order: int = 0


class ReferenceColumnOut(BaseModel):
    """Описание колонки справочной таблицы (для построения редактора)."""

    key: str
    label: str
    type: str  # "text" | "integer" | "boolean"


class ReferenceTableOut(BaseModel):
    """Описание справочной таблицы (список для переключателя)."""

    key: str
    title: str
    columns: list[ReferenceColumnOut]


class ReferenceRowIn(BaseModel):
    """Тело строки справочника; конкретные поля валидируются схемой таблицы."""

    model_config = ConfigDict(extra="allow")


class ReferenceRowsOut(BaseModel):
    """Список строк справочника (все колонки таблицы как dict)."""

    total: int
    items: list[dict[str, Any]]


class OptionOut(BaseModel):
    """Опция каталога в личном кабинете: что доступно и почему.

    ``enabled`` — фактическая доступность для пользователя сейчас (бесплатные —
    всегда; платные — триал или активный аккаунт). ``account_enabled`` —
    включена ли платная опция в активном аккаунте (для бесплатных None).
    ``available`` — реализована ли опция системой (geo_premium отложена).
    """

    key: str
    title: str
    description: str
    group: str
    available: bool
    requires_competencies: bool = False
    enabled: bool = False
    account_enabled: bool | None = None


class AccountOut(BaseModel):
    """Карточка аккаунта пользователя (набор платных опций)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    options: dict[str, bool]
    created_at: datetime
    updated_at: datetime


class AccountIn(BaseModel):
    """Создание аккаунта (личный кабинет / админ)."""

    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Укажите имя аккаунта")
        return value


class AccountUpdateIn(BaseModel):
    """Переименование аккаунта и/или обновление переключателей платных опций.

    Передаются только платные опции каталога (бесплатные доступны всегда и не
    переключаются). Отложенные опции (geo_premium) включать нельзя.
    """

    name: str | None = Field(default=None, min_length=1, max_length=128)
    options: dict[str, bool] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Укажите имя аккаунта")
        return value

    @field_validator("options")
    @classmethod
    def _options(cls, value: dict[str, bool] | None) -> dict[str, bool] | None:
        if value is None:
            return None
        out: dict[str, bool] = {}
        for key, enabled in value.items():
            option = option_by_key(key)
            if option is None:
                raise ValueError(f"Неизвестная опция: {key}")
            if key not in PAID_KEYS:
                raise ValueError(f"Бесплатную опцию «{option.title}» нельзя переключать")
            if not option.available:
                raise ValueError(f"Опция «{option.title}» пока недоступна")
            out[key] = bool(enabled)
        return out


class TrialStatusOut(BaseModel):
    """Триал-режим пользователя в личном кабинете."""

    enabled: bool
    trial_end_at: datetime | None = None
    days_left: int | None = None


class CabinetOut(BaseModel):
    """Личный кабинет: пользователь, триал, аккаунты и каталог опций."""

    user_id: int
    username: str
    email: str | None = None
    roles: list[str]
    trial: TrialStatusOut
    active_account_id: int | None = None
    accounts: list[AccountOut]
    catalog: list[OptionOut]


class UserAccountsOut(BaseModel):
    """Аккаунты пользователя для админа (список + состояние триала)."""

    user_id: int
    username: str
    active_account_id: int | None = None
    accounts: list[AccountOut]
    trial: TrialStatusOut


class PasswordChangeIn(BaseModel):
    """Смена собственного пароля (личный кабинет)."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    new_password_confirm: str = Field(min_length=8)

    @model_validator(mode="after")
    def _passwords_match(self) -> PasswordChangeIn:
        if self.new_password != self.new_password_confirm:
            raise ValueError("new_password_confirm не совпадает с new_password")
        return self


class UserTrialIn(BaseModel):
    """Управление триалом пользователя администратором.

    ``days`` — установить триал на N суток от текущего момента (перекрывает
    ``trial_end_at``); ``trial_end_at`` — конкретная дата; null (и days null) —
    отключить триал.
    """

    trial_end_at: datetime | None = None
    days: int | None = Field(default=None, ge=1, le=3650)
