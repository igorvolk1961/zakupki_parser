"""Модели эксплуатационной (devops) конфигурации (config_ops.yaml).

Здесь собраны параметры инфраструктуры и эксплуатации, которыми управляют
devops: таймер цикла, подключение к БД, уведомления, каталог выгрузки и
параметры circuit breaker. Аналитики такие параметры не трогают — их домен
описан в ``config/models/service.py`` (config_service.yaml).
"""

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
    # Стадия Fit включается/выключается целиком: при false уведомление после
    # Fit не отправляется вовсе (порог notify_min_fit_score игнорируется).
    notify_fit_enabled: bool = Field(
        default=True,
        description="отправлять ли уведомление после стадии Fit; false — не отправлять",
    )
    notify_min_fit_score: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description=(
            "порог: уведомлять после стадии Fit, только если fit_score >= "
            "notify_min_fit_score (fit_score на шкале 0..1; проверяется в POST /score "
            "после прихода внешнего скора, ADR-7)"
        ),
    )
    # Стадия P(win) включается/выключается целиком: при false уведомление после
    # P(win) не отправляется вовсе (порог notify_min_pwin игнорируется).
    notify_pwin_enabled: bool = Field(
        default=True,
        description="отправлять ли уведомление после стадии P(win); false — не отправлять",
    )
    notify_min_pwin: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description=(
            "порог: уведомлять после стадии P(win), только если возвращаемое "
            "значение p_win >= notify_min_pwin; 0 — не ограничивать"
        ),
    )
    # Стадия Margin включается/выключается целиком: при false уведомление после
    # Margin не отправляется вовсе (порог notify_min_margin игнорируется).
    notify_margin_enabled: bool = Field(
        default=True,
        description="отправлять ли уведомление после стадии Margin; false — не отправлять",
    )
    notify_min_margin: float = Field(
        default=0.0,
        ge=0,
        description=(
            "порог: уведомлять после стадии Margin, только если возвращаемое "
            "значение margin (руб.) >= notify_min_margin; 0 — не ограничивать"
        ),
    )

    @model_validator(mode="after")
    def _check_chat_ids(self) -> NotificationsConfig:
        """chat_id может быть не задан: он подставляется из env (ZAKUPKI_MAX_CHAT_ID /
        ZAKUPKI_TELEGRAM_CHAT_ID) в loader. Если его нет — конфиг валиден, а бэкенд
        при отправке пропустит уведомление с предупреждением (см. notify.py).
        """
        return self


class OpsConfig(_BaseConfig):
    """Эксплуатационная конфигурация: таймер, БД, уведомления, выгрузка, circuit breaker."""

    timeout_seconds: int = Field(default=3600, ge=1)
    db: DbConfig = Field(default_factory=DbConfig)
    export_dir: str = Field(
        default="data/export",
        description=(
            "каталог на сервере для выгрузки БД в CSV (кнопка «Выгрузить CSV»); "
            "создаётся автоматически при выгрузке"
        ),
    )
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout_seconds: float = Field(default=60.0, ge=1)
