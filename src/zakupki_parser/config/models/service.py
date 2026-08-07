"""Модели сервисной конфигурации (config_service.yaml)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _BaseConfig(BaseModel):
    """Базовый класс конфигурации: неизвестные ключи — ошибка (reject опечаток)."""

    model_config = ConfigDict(extra="forbid")


class DbConfig(_BaseConfig):
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


class WebhookConfig(_BaseConfig):
    """Параметры webhook-уведомлений."""

    enabled: bool = Field(default=False)
    url: str | None = None
    token: str | None = None
    timeout_seconds: float = Field(default=10.0, ge=0)


class TelegramConfig(_BaseConfig):
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


class MaxConfig(_BaseConfig):
    """Параметры уведомлений в мессенджер MAX.

    ``chat_id`` — числовой id канала (int64), получается через подписку на
    события (``bot_added``/``bot_started``). Токен не хранится в YAML — секрет,
    подкладывается из env ``ZAKUPKI_MAX_TOKEN`` в ``config/loader.py``.
    """

    enabled: bool = Field(default=False)
    chat_id: str | None = Field(
        default=None, description="числовой id канала (int64), из подписки на события"
    )
    timeout_seconds: float = Field(default=10.0, ge=0)
    insecure_tls: bool = Field(
        default=True,
        description=(
            "не проверять TLS-сертификат MAX (сертификат Минцифры может отсутствовать "
            "в доверенных); по умолчанию выключено"
        ),
    )
    token: str | None = Field(
        default=None, description="access_token бота, из env; не сериализуется в YAML"
    )


class NotificationsConfig(_BaseConfig):
    """Настройки уведомлений: выбор бэкенда и его параметры."""

    backend: Literal["telegram", "webhook", "max", "none"] = Field(
        default="webhook",
        description=(
            "бэкенд уведомлений; 'none' — отключить оповещения полностью "
            "(Notifier не будет создавать ни один бэкенд)"
        ),
    )
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    max: MaxConfig = Field(default_factory=MaxConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    notify_min_score: float = Field(
        default=0.0,
        ge=0,
        description=(
            "порог: уведомлять только если финальный score >= notify_min_score "
            "(проверяется в POST /score после прихода внешнего скора, ADR-7)"
        ),
    )

    @model_validator(mode="after")
    def _check_chat_ids(self) -> NotificationsConfig:
        """chat_id может быть не задан: он подставляется из env (ZAKUPKI_MAX_CHAT_ID /
        ZAKUPKI_TELEGRAM_CHAT_ID) в loader. Если его нет — конфиг валиден, а бэкенд
        при отправке пропустит уведомление с предупреждением (см. notify.py).
        """
        return self


class SiteServiceEntry(_BaseConfig):
    """Одна запись в списке сайтов для периодического обхода."""

    platform_id: str = Field(description="ключ площадки в config_dom.yaml")
    enabled: bool = Field(default=True)


class StopConditions(_BaseConfig):
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


class SearchCriteria(_BaseConfig):
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
    fz44: bool = Field(
        default=True,
        description="включать закупки по 44-ФЗ (применяется на ЕИС: fz44=on)",
    )
    fz223: bool = Field(
        default=True,
        description="включать закупки по 223-ФЗ (применяется на ЕИС: fz223=on)",
    )


class ServiceConfig(_BaseConfig):
    """Сервисная конфигурация: таймер, список сайтов, пороги, флаги."""

    timeout_seconds: int = Field(default=3600, ge=1)
    sites: list[SiteServiceEntry] = Field(default_factory=list)
    default_cutoff_days: int = Field(
        default=7, ge=0, description="порог 'дата последней обработанной записи' в днях"
    )
    search_criteria: SearchCriteria = Field(
        default_factory=SearchCriteria, description="критерии поиска (тематика фильтра)"
    )
    db: DbConfig = Field(default_factory=DbConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout_seconds: float = Field(default=60.0, ge=1)
