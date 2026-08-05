"""Модели сервисной конфигурации (config_service.yaml)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class TelegramConfig(BaseModel):
    """Параметры Telegram-уведомлений.

    ``chat_id`` — адрес канала: ``@username`` для публичного или числовой id
    (отрицательный, например ``-1001234567890``) для приватного.
    Токен бота не хранится здесь в YAML — он секрет и подкладывается из env
    ``ZAKUPKI_TELEGRAM_TOKEN`` в ``config/loader.py``.
    """

    enabled: bool = Field(default=False)
    chat_id: str | None = Field(
        default=None, description="@username канала или числовой id (для приватного)"
    )
    timeout_seconds: float = Field(default=10.0, ge=0)
    token: str | None = Field(
        default=None, description="токен бота, из env; не сериализуется в YAML"
    )


class NotificationsConfig(BaseModel):
    """Настройки уведомлений: выбор бэкенда и его параметры."""

    backend: Literal["telegram", "webhook"] = Field(default="webhook")
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)

    @model_validator(mode="after")
    def _check_telegram_chat_id(self) -> NotificationsConfig:
        """Telegram требует адрес канала: включённый бэкенд без chat_id — ошибка."""
        if self.backend == "telegram" and self.telegram.enabled and not self.telegram.chat_id:
            raise ValueError(
                "notifications.backend=telegram и telegram.enabled=true, но "
                "telegram.chat_id не задан (нужен '@username' или числовой id канала)"
            )
        return self


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
    min_deadline_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "если задано — не обрабатывать заявку, до срока подачи которой осталось "
            "меньше указанного числа календарных дней (нужно время на подготовку заявки)"
        ),
    )


class SearchCriteria(BaseModel):
    """Бизнес-критерии поиска — задаются в config_service.yaml в ОБОБЩЁННЫХ терминах.

    Эти поля платформонезависимы (ОКПД2, НМЦК, ключевые слова, регионы — это
    понятия закупочной тематики). Конкретная привязка каждого критерия к параметрам
    URL-запроса или DOM-селекторам площадки выполняется в config_dom.yaml
    (``search.criteria_map``), поэтому здесь нет ни селекторов, ни имён query-параметров.
    """

    okpd_codes: list[str] = Field(
        default_factory=list,
        description=(
            "коды ОКПД2 (тематика). Резолвятся в пути узлов дерева площадки "
            "через маппинг (search.okpd_tree_file); выбор предка включает потомков"
        ),
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "ключевые слова для поиска в предмете/наименовании закупки (ИИ, "
            "автоматизация, разработка и т.п.). Пусто — поиск по словам не применяется."
        ),
    )
    nmck_min: float | None = Field(
        default=None,
        ge=0,
        description="минимальная НМЦК (отсекает мелкие лоты, не окупающие консалтинг)",
    )
    nmck_max: float | None = Field(
        default=None,
        ge=0,
        description="максимальная НМЦК",
    )
    region_codes: list[str] = Field(
        default_factory=list,
        description=(
            "коды регионов для фильтрации. Резолвятся в пути узлов дерева площадки "
            "через маппинг (search.region_tree_file, формат как у ОКПД2)"
        ),
    )


class StorageConfig(BaseModel):
    """Хранилище скачанных файлов (например, только технического задания).

    ``type``: ``local`` — каталог ``documents_dir``; ``s3`` — MinIO/совместимое
    объектное хранилище (в БД пишется URL объекта, а не бинарник).
    """

    type: Literal["local", "s3"] = Field(default="local")
    endpoint: str = Field(default="http://localhost:9000", description="для type=s3 (MinIO)")
    access_key: str | None = Field(default=None)
    secret_key: str | None = Field(default=None)
    bucket: str = Field(default="zakupki-documents")
    secure: bool = Field(default=False, description="HTTPS для s3")
    region: str = Field(default="us-east-1")

    @model_validator(mode="after")
    def _check_s3_params(self) -> StorageConfig:
        """S3 опционален: при type=local параметры не нужны; при type=s3 — обязательны."""
        if self.type == "s3":
            missing = [
                name
                for name, value in (
                    ("endpoint", self.endpoint),
                    ("access_key", self.access_key),
                    ("secret_key", self.secret_key),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"storage.type=s3, но не заданы: {', '.join(missing)}")
        return self


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
    documents_dir: str = Field(default="documents")
    data_dir: str = Field(default="data")
    db: DbConfig = Field(default_factory=DbConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout_seconds: float = Field(default=60.0, ge=1)

    @model_validator(mode="after")
    def _check_ts_keywords(self) -> ServiceConfig:
        """Защита: флаг «только ТЗ» без ключевых слов — ошибка конфигурации."""
        if self.download_technical_spec_only and not self.technical_spec_keywords:
            raise ValueError(
                "download_technical_spec_only=true, но technical_spec_keywords пуст — "
                "не будет скачано ни одного файла"
            )
        return self
