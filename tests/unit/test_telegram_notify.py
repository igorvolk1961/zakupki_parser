"""Unit-тесты уведомлений: рендер карточки, Telegram/webhook-бэкенды, диспетчер."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx
import pytest

from zakupki_parser.config.models import (
    MaxConfig,
    NotificationsConfig,
    TelegramConfig,
    WebhookConfig,
)
from zakupki_parser.notify import (
    MaxBackend,
    Notifier,
    TelegramBackend,
    WebhookBackend,
    render_telegram_message,
)

_RECORD: dict[str, Any] = {
    "number": "12345",
    "source_platform": "zakupki_mos",
    "subject": 'Разработка ПО <и> "автоматизация" &',
    "customer": "Заказчик",
    "law": "223-ФЗ",
    "nmck": 1500000.0,
    "publication_date": "2026-08-01T10:00:00",
    "deadline": "2026-08-10T10:00:00",
    "score": 0.95,
    "url": "https://example.com/purchase/12345",
}


def _telegram_cfg(**overrides: Any) -> TelegramConfig:
    base: dict[str, Any] = {"enabled": True, "chat_id": "@chan", "token": "123:ABC"}
    base.update(overrides)
    return TelegramConfig(**base)


class TestRenderTelegramMessage:
    def test_escapes_html_entities(self) -> None:
        text = render_telegram_message(_RECORD)
        assert "&amp;" in text
        assert "&lt;" in text
        assert "&gt;" in text
        assert "&quot;" in text

    def test_escaped_subject_not_present_raw(self) -> None:
        text = render_telegram_message(_RECORD)
        assert "Разработка ПО <и>" not in text
        assert "Разработка ПО &lt;и&gt;" in text

    def test_skips_empty_fields(self) -> None:
        record = dict(_RECORD)
        record.pop("customer")
        record["score"] = None
        text = render_telegram_message(record)
        assert "Заказчик" not in text
        assert "Оценка" not in text
        assert "Площадка" in text

    def test_contains_link(self) -> None:
        text = render_telegram_message(_RECORD)
        assert "https://example.com/purchase/12345" in text
        assert "<a href=" in text


class TestTelegramBackend:
    async def test_send_posts_correct_payload(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["json"] = request.content
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        backend = TelegramBackend(_telegram_cfg())
        transport = httpx.MockTransport(handler)
        await backend.send(_RECORD, transport=transport)

        assert captured["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
        payload = json.loads(captured["json"])
        assert payload["chat_id"] == "@chan"
        assert payload["parse_mode"] == "HTML"
        assert payload["disable_web_page_preview"] is True
        assert "12345" in payload["text"]

    async def test_send_raises_on_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        backend = TelegramBackend(_telegram_cfg())
        with pytest.raises(httpx.HTTPStatusError):
            await backend.send(_RECORD, transport=httpx.MockTransport(handler))

    async def test_send_raises_on_ok_false(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": False, "description": "bad request"})

        backend = TelegramBackend(_telegram_cfg())
        with pytest.raises(ValueError):
            await backend.send(_RECORD, transport=httpx.MockTransport(handler))

    async def test_send_raises_without_token(self) -> None:
        backend = TelegramBackend(_telegram_cfg(token=None))
        with pytest.raises(ValueError):
            await backend.send(_RECORD)


class TestMaxBackend:
    async def test_send_posts_correct_payload(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = request.headers
            captured["json"] = request.content
            return httpx.Response(200, json={"message": {"id": 1}})

        cfg = MaxConfig(enabled=True, chat_id="123456789012345678", token="SECRET")
        backend = MaxBackend(cfg)
        await backend.send(_RECORD, transport=httpx.MockTransport(handler))

        assert captured["url"] == (
            "https://platform-api2.max.ru/messages?chat_id=123456789012345678"
        )
        assert captured["headers"]["Authorization"] == "SECRET"
        payload = json.loads(captured["json"])
        assert payload["format"] == "html"
        assert payload["disable_link_preview"] is True
        assert "12345" in payload["text"]

    async def test_send_raises_on_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        backend = MaxBackend(MaxConfig(enabled=True, chat_id="1", token="SECRET"))
        with pytest.raises(httpx.HTTPStatusError):
            await backend.send(_RECORD, transport=httpx.MockTransport(handler))

    async def test_send_raises_without_token(self) -> None:
        backend = MaxBackend(MaxConfig(enabled=True, chat_id="1", token=None))
        with pytest.raises(ValueError):
            await backend.send(_RECORD)

    def test_insecure_tls_defaults_to_true(self) -> None:
        assert MaxConfig().insecure_tls is True


class TestWebhookBackend:
    async def test_send_posts_json_with_auth(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = request.headers
            captured["json"] = request.content
            return httpx.Response(200)

        cfg = WebhookConfig(enabled=True, url="https://hook.example/x", token="sekret")
        backend = WebhookBackend(cfg)
        await backend.send(_RECORD, transport=httpx.MockTransport(handler))

        assert captured["url"] == "https://hook.example/x"
        assert captured["headers"]["Authorization"] == "Bearer sekret"
        assert json.loads(captured["json"])["number"] == "12345"


class TestNotifier:
    def test_no_backends_when_disabled(self) -> None:
        cfg = NotificationsConfig(
            backend="webhook",
            telegram=TelegramConfig(enabled=True, chat_id="@chan"),
            webhook=WebhookConfig(enabled=False),
        )
        assert Notifier(cfg)._backends == []

    def test_telegram_backend_selected(self) -> None:
        cfg = NotificationsConfig(
            backend="telegram",
            telegram=TelegramConfig(enabled=True, chat_id="@chan", token="123:ABC"),
        )
        notifier = Notifier(cfg)
        assert len(notifier._backends) == 1
        assert isinstance(notifier._backends[0], TelegramBackend)

    def test_max_backend_selected(self) -> None:
        cfg = NotificationsConfig(
            backend="max",
            max=MaxConfig(enabled=True, chat_id="123", token="SECRET"),
        )
        notifier = Notifier(cfg)
        assert len(notifier._backends) == 1
        assert isinstance(notifier._backends[0], MaxBackend)

    async def test_backend_error_logged_not_raised(self, caplog: Any) -> None:
        class BoomBackend:
            async def send(self, record: dict[str, Any]) -> None:
                raise RuntimeError("boom")

        notifier = Notifier(
            NotificationsConfig(backend="webhook", webhook=WebhookConfig(enabled=False))
        )
        notifier._backends = cast(
            list[TelegramBackend | MaxBackend | WebhookBackend], [BoomBackend()]
        )
        with caplog.at_level(logging.WARNING, logger="zakupki_parser.notify"):
            await notifier.notify(_RECORD)
        assert "Не удалось отправить" in caplog.text

    async def test_dispatcher_calls_backend(self) -> None:
        calls: list[dict[str, Any]] = []

        class SpyBackend:
            async def send(self, record: dict[str, Any]) -> None:
                calls.append(record)

        notifier = Notifier(
            NotificationsConfig(backend="webhook", webhook=WebhookConfig(enabled=False))
        )
        notifier._backends = cast(
            list[TelegramBackend | MaxBackend | WebhookBackend], [SpyBackend()]
        )
        await notifier.notify(_RECORD)
        assert calls == [_RECORD]


class TestNotificationsConfigValidation:
    def test_telegram_enabled_without_chat_id_allowed(self) -> None:
        # chat_id может быть не задан (подставляется из env); бэкенд пропустит
        # уведомление при отправке.
        cfg = NotificationsConfig(
            backend="telegram",
            telegram=TelegramConfig(enabled=True, token="123:ABC"),
        )
        assert cfg.telegram.chat_id is None

    def test_telegram_enabled_with_chat_id_ok(self) -> None:
        cfg = NotificationsConfig(
            backend="telegram",
            telegram=TelegramConfig(enabled=True, chat_id="-1001234567890", token="123:ABC"),
        )
        assert cfg.telegram.chat_id == "-1001234567890"

    def test_webhook_backend_ignores_telegram_chat_id(self) -> None:
        cfg = NotificationsConfig(
            backend="webhook",
            telegram=TelegramConfig(enabled=True),
        )
        assert cfg.backend == "webhook"

    def test_max_enabled_without_chat_id_allowed(self) -> None:
        cfg = NotificationsConfig(
            backend="max",
            max=MaxConfig(enabled=True, token="SECRET"),
        )
        assert cfg.max.chat_id is None

    def test_max_enabled_with_chat_id_ok(self) -> None:
        cfg = NotificationsConfig(
            backend="max",
            max=MaxConfig(enabled=True, chat_id="123", token="SECRET"),
        )
        assert cfg.max.chat_id == "123"
