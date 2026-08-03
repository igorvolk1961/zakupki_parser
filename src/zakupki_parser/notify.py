"""Уведомления о появлении новой записи о закупке (webhook).

Сейчас — заглушка/лог. Реальная отправка HTTP POST реализуется позже (см. TODO).
"""

from __future__ import annotations

import logging
from typing import Any

from zakupki_parser.config.models import WebhookConfig

logger = logging.getLogger(__name__)


class Notifier:
    """Оповещает подписчиков о новой записи о закупке."""

    def __init__(self, cfg: WebhookConfig) -> None:
        self._cfg = cfg

    async def notify(self, record: dict[str, Any]) -> None:
        """Отправляет уведомление (заглушка: логирует)."""
        number = record.get("number")
        platform = record.get("source_platform")
        if not self._cfg.enabled:
            logger.info(
                "webhook отключён; пропущено уведомление о заявке %s (%s)",
                number,
                platform,
            )
            return
        logger.info("webhook: новая заявка %s (%s), payload=%r", number, platform, record)
