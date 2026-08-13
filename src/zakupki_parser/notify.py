"""Уведомления подписчиков о новых записях о закупке.

Плагинный ``Notifier``-диспетчер выбирает активный бэкенд из конфигурации
(``notifications.backend``): ``telegram`` (``sendMessage`` через REST API),
``max`` (``POST /messages`` мессенджера MAX) или ``webhook`` (POST JSON на
произвольный URL).

Ошибки отправки логируются как ``warning`` и не пробрасываются наружу, чтобы
сбой уведомления не ломал проход парсера (вежливая деградация).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from zakupki_parser.config.models import (
    MaxConfig,
    NotificationsConfig,
    TelegramConfig,
    WebhookConfig,
)
from zakupki_parser.parser.json_utils import json_safe

logger = logging.getLogger(__name__)

_HTML_ESCAPE = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
}


def _html_escape(text: str) -> str:
    """Экранирует HTML-сущности (значения приходят со скрейпленных страниц)."""
    return "".join(_HTML_ESCAPE.get(ch, ch) for ch in text)


def _as_text(value: Any) -> str | None:
    """Приводит значение записи к строке; ``None``/пусто → пропуск."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def render_telegram_message(record: dict[str, Any]) -> str:
    """HTML-карточка закупки для ``sendMessage`` (``parse_mode="HTML"``).

    Пустые поля пропускаются. Все значения экранируются (контент со скрейпленных
    страниц считается ненадёжным).
    """
    fields: list[tuple[str, Any]] = [
        ("№", "number"),
        ("Площадка", "source_platform"),
        ("Предмет", "subject"),
        ("Заказчик", "customer"),
        ("Закон", "law"),
        ("НМЦК", "nmck"),
        ("Опубликовано", "publication_date"),
        ("Срок подачи", "deadline"),
        ("Fit", "fit_score"),
        ("Оценка", "score"),
    ]
    lines: list[str] = []
    for label, key in fields:
        value = _as_text(record.get(key))
        if value is None:
            continue
        lines.append(f"{label}: {_html_escape(value)}")
    url = _as_text(record.get("url"))
    if url is not None:
        escaped_url = _html_escape(url)
        lines.append(f'<a href="{escaped_url}">{escaped_url}</a>')
    return "\n".join(lines)


class TelegramBackend:
    """Отправляет карточку закупки в Telegram-канал через REST API."""

    def __init__(self, cfg: TelegramConfig) -> None:
        self._chat_id = cfg.chat_id
        self._token = cfg.token
        self._timeout = cfg.timeout_seconds
        self._url = f"https://api.telegram.org/bot{cfg.token}/sendMessage"

    async def send(
        self, record: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Шлёт ``sendMessage`` с HTML-карточкой."""
        if not self._token:
            raise ValueError(
                "telegram.enabled=true, но не задан токен бота (env ZAKUPKI_TELEGRAM_TOKEN)"
            )
        if not self._chat_id:
            raise ValueError("telegram.chat_id не задан — уведомление пропущено")
        payload = {
            "chat_id": self._chat_id,
            "text": render_telegram_message(record),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            resp = await client.post(self._url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise ValueError(f"Telegram вернул ошибку: {data!r}")


class MaxBackend:
    """Отправляет карточку закупки в канал мессенджера MAX через Bot API.

    Эндпоинт: ``POST https://platform-api2.max.ru/messages?chat_id={chat_id}``.
    Токен передаётся в заголовке ``Authorization`` (не в query). Формат — HTML.
    """

    _BASE_URL = "https://platform-api2.max.ru"

    def __init__(self, cfg: MaxConfig) -> None:
        self._cfg = cfg
        self._chat_id = cfg.chat_id
        self._token = cfg.token
        self._timeout = cfg.timeout_seconds

    async def send(
        self, record: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Шлёт HTML-карточку в канал MAX."""
        if not self._token:
            raise ValueError("max.enabled=true, но не задан токен бота (env ZAKUPKI_MAX_TOKEN)")
        if not self._chat_id:
            raise ValueError("max.chat_id не задан — уведомление пропущено")
        payload = {
            "text": render_telegram_message(record),
            "format": "html",
            "disable_link_preview": True,
        }
        headers = {"Authorization": self._token}
        url = f"{self._BASE_URL}/messages?chat_id={self._chat_id}"
        verify = not self._cfg.insecure_tls
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=transport, verify=verify
        ) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()


class WebhookBackend:
    """POST JSON-карточки закупки на произвольный URL."""

    def __init__(self, cfg: WebhookConfig) -> None:
        self._url = cfg.url
        self._token = cfg.token
        self._timeout = cfg.timeout_seconds

    async def send(
        self, record: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Шлёт JSON-карточку; при заданном ``token`` — как Bearer-заголовок."""
        if not self._url:
            raise ValueError("webhook.enabled=true, но url не задан")
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            resp = await client.post(self._url, json=json_safe(record), headers=headers)
            resp.raise_for_status()


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
                "уведомления отключены; пропущена заявка %s (%s)",
                record.get("number"),
                record.get("source_platform"),
            )
            return
        for backend in self._backends:
            try:
                await backend.send(record)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Не удалось отправить уведомление о заявке %s (%s): %s",
                    record.get("number"),
                    record.get("source_platform"),
                    exc,
                )
