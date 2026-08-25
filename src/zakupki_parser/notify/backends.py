"""Бэкенды уведомлений: Telegram, MAX, webhook."""

from __future__ import annotations

from typing import Any

import httpx

from zakupki_parser.config.models import MaxConfig, TelegramConfig, WebhookConfig
from zakupki_parser.notify.render import render_telegram_message
from zakupki_parser.parser.json_utils import json_safe


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
