"""Диспетчер уведомлений: выбирает активный бэкенд и рассылает карточку."""

from __future__ import annotations

import logging
from typing import Any

from zakupki_parser.config.models import NotificationsConfig
from zakupki_parser.notify.backends import MaxBackend, TelegramBackend, WebhookBackend

logger = logging.getLogger(__name__)


class Notifier:
    """Диспетчер уведомлений: собирает активные бэкенды и рассылает карточку.

    Бэкенд активен, только если он выбран в ``notifications.backend`` и у него
    включён собственный флаг ``enabled``.
    """

    def __init__(self, cfg: NotificationsConfig) -> None:
        self._backends: list[TelegramBackend | MaxBackend | WebhookBackend] = []
        if cfg.backend == "telegram" and cfg.telegram.enabled:
            self._backends.append(TelegramBackend(cfg.telegram))
        if cfg.backend == "max" and cfg.max.enabled:
            self._backends.append(MaxBackend(cfg.max))
        if cfg.backend == "webhook" and cfg.webhook.enabled:
            self._backends.append(WebhookBackend(cfg.webhook))

    async def notify(self, record: dict[str, Any]) -> None:
        """Рассылает уведомление всем активным бэкендам; ошибки логируются."""
        if not self._backends:
            logger.info(
                "уведомления отключены; пропущена закупка %s (%s)",
                record.get("number"),
                record.get("platform_id"),
            )
            return
        for backend in self._backends:
            try:
                await backend.send(record)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Не удалось отправить уведомление о закупке %s (%s): %s",
                    record.get("number"),
                    record.get("platform_id"),
                    exc,
                )
