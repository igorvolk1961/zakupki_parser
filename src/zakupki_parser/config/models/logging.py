"""Модель конфигурации логирования (config_log.yaml).

Переиспользует общую схему ``scoring_common.logging.LoggingSettings``, чтобы
управление логами парсера и фоновых сервисов было единообразным.
"""

from __future__ import annotations

from scoring_common.logging import LoggingSettings


class LoggingConfig(LoggingSettings):
    """Конфигурация логирования."""
