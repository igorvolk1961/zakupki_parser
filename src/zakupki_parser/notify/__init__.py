"""Уведомления подписчиков о новых записях о закупке.

Плагинный ``Notifier``-диспетчер выбирает активный бэкенд из конфигурации
(``notifications.backend``): ``telegram`` (``sendMessage`` через REST API),
``max`` (``POST /messages`` мессенджера MAX) или ``webhook`` (POST JSON на
произвольный URL).

Ошибки отправки логируются как ``warning`` и не пробрасываются наружу, чтобы
сбой уведомления не ломал проход парсера (вежливая деградация).

Реализация разбита на подпакеты: ``render`` (HTML-карточка), ``backends``
(Telegram/MAX/webhook), ``notifier`` (диспетчер). Здесь — реэкспорт для
совместимости с прежним модулем ``notify.py``.
"""

from __future__ import annotations

from zakupki_parser.notify.backends import MaxBackend, TelegramBackend, WebhookBackend
from zakupki_parser.notify.notifier import Notifier
from zakupki_parser.notify.render import render_telegram_message

__all__ = [
    "MaxBackend",
    "Notifier",
    "TelegramBackend",
    "WebhookBackend",
    "render_telegram_message",
]
