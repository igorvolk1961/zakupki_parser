"""Модели эксплуатационной (devops) конфигурации (config_ops.yaml).

Модели разбиты по поддоменам (подпакеты): ``base`` (базовый класс с reject
опечаток), ``db`` (подключение к БД), ``notifications`` (бэкенды и пороги
уведомлений), ``auth`` (параметры аутентификации), ``runtime`` (корневая
OpsConfig). Здесь — реэкспорт для совместимости с прежним модулем
``config/models/ops.py``.
"""

from __future__ import annotations

from zakupki_parser.config.models.ops.auth import AuthConfig
from zakupki_parser.config.models.ops.db import DbConfig
from zakupki_parser.config.models.ops.notifications import (
    MaxConfig,
    NotificationsConfig,
    TelegramConfig,
    WebhookConfig,
)
from zakupki_parser.config.models.ops.runtime import OpsConfig

__all__ = [
    "AuthConfig",
    "DbConfig",
    "MaxConfig",
    "NotificationsConfig",
    "OpsConfig",
    "TelegramConfig",
    "WebhookConfig",
]
