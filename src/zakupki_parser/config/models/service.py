"""Модели сервисной конфигурации (config_service.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DbConfig(BaseModel):
    """Параметры подключения к базе данных."""

    dsn: str = Field(default="postgresql://postgres:postgres@localhost:5432/zakupki")
    enabled: bool = Field(default=True)
    connect_timeout_seconds: float = Field(default=5.0, ge=0)
    pool_min: int = Field(default=1, ge=0)
    pool_max: int = Field(default=5, ge=1)
    retry_max_attempts: int = Field(
        default=3, ge=1, description="повторы записи при транзиентной ошибке БД"
    )
    retry_backoff_seconds: float = Field(
        default=1.0, ge=0, description="базовая пауза между повторами (растёт линейно)"
    )


class WebhookConfig(BaseModel):
    """Параметры webhook-уведомлений."""

    enabled: bool = Field(default=False)
    url: str | None = None
    token: str | None = None
    timeout_seconds: float = Field(default=10.0, ge=0)


class SiteServiceEntry(BaseModel):
    """Одна запись в списке сайтов для периодического обхода."""

    platform_id: str = Field(description="ключ площадки в config_dom.yaml")
    enabled: bool = Field(default=True)


class StopConditions(BaseModel):
    """Набор флагов-условий прекращения обработки очередной заявки.

    Каждый флаг — это условие, при котором заявка пропускается (не сохраняется
    и не уведомляется). Набор расширяется добавлением новых флагов.
    """

    enabled: bool = Field(default=True, description="главный переключатель условий")
    deadline_not_expired: bool = Field(
        default=True,
        description=(
            "не обрабатывать заявку, если срок приёма заявок (переменная 'deadline' "
            "из config_dom.yaml) истёк к текущей дате"
        ),
    )


class SearchCriteria(BaseModel):
    """Бизнес-критерии поиска (задаются в config_service.yaml)."""

    okpd_codes: list[str] = Field(
        default_factory=list,
        description=(
            "коды ОКПД2 для фильтрации; резолвятся в okpdPaths через маппинг "
            "площадки (search.okpd_tree_file); выбор предка включает потомков"
        ),
    )


class ServiceConfig(BaseModel):
    """Сервисная конфигурация: таймер, список сайтов, пороги, флаги."""

    timeout_seconds: int = Field(default=3600, ge=1)
    sites: list[SiteServiceEntry] = Field(default_factory=list)
    default_cutoff_days: int = Field(
        default=7, ge=0, description="порог 'дата последней обработанной записи' в днях"
    )
    search_criteria: SearchCriteria = Field(
        default_factory=SearchCriteria, description="критерии поиска (тематика фильтра)"
    )
    download_files: bool = Field(default=False)
    download_technical_spec_only: bool = Field(
        default=False,
        description=(
            "скачивать только файлы, в имени которых есть ключевые слова "
            "(например, только техническое задание)"
        ),
    )
    technical_spec_keywords: list[str] = Field(
        default_factory=lambda: ["техническое задание"],
        description="тексты для поиска в имени файла при download_technical_spec_only",
    )
    delete_files_after_processing: bool = Field(default=True)
    documents_dir: str = Field(default="documents")
    data_dir: str = Field(default="data")
    db: DbConfig = Field(default_factory=DbConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout_seconds: float = Field(default=60.0, ge=1)
