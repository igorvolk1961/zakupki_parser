"""Unit-тесты OCR-фолбэка для сканов PDF (scoring_common.ocr)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scoring_common import ocr as ocr_mod
from scoring_common.ocr import YandexOcrClient, YandexOcrSettings, _text_from_yandex_response


@pytest.fixture(autouse=True)
def _reset_ocr_singleton():
    """Ленивый синглтон модуля переживает между тестами — сбрасываем."""
    ocr_mod.set_client(None)
    yield
    ocr_mod.set_client(None)


def test_yandex_client_requires_credentials() -> None:
    with pytest.raises(ValueError):
        YandexOcrClient(YandexOcrSettings(api_key=None, folder_id="f1"))
    with pytest.raises(ValueError):
        YandexOcrClient(YandexOcrSettings(api_key="k1", folder_id=None))


def test_text_from_yandex_response_joins_pages_blocks_lines() -> None:
    data: dict[str, Any] = {
        "result": {
            "textAnnotation": {
                "pages": [
                    {
                        "blocks": [
                            {"lines": [{"words": [{"text": "Привет"}, {"text": "мир"}]}]},
                        ]
                    },
                    {
                        "blocks": [
                            {
                                "lines": [
                                    {"words": [{"text": "Вторая"}]},
                                    {"words": [{"text": "строка"}]},
                                ]
                            },
                        ]
                    },
                ]
            }
        }
    }
    assert _text_from_yandex_response(data) == "Привет мир\nВторая\nстрока"


def test_text_from_yandex_response_empty_pages_returns_none() -> None:
    assert _text_from_yandex_response({"result": {"textAnnotation": {"pages": []}}}) is None
    assert _text_from_yandex_response({}) is None


def test_yandex_client_recognize_pdf_sends_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запрос: mimeType=PDF, base64-контент, заголовки Api-Key + x-folder-id."""
    settings = YandexOcrSettings(api_key="k1", folder_id="f1", language_codes=["ru"], model="page")
    client = YandexOcrClient(settings)
    captured: dict[str, Any] = {}

    def fake_post(url: str, json: dict, headers: dict) -> httpx.Response:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "result": {
                    "textAnnotation": {
                        "pages": [{"blocks": [{"lines": [{"words": [{"text": "ok"}]}]}]}]
                    }
                }
            },
        )

    monkeypatch.setattr(client._client, "post", fake_post)
    assert client.recognize_pdf(b"%PDF-1.7-raw-bytes") == "ok"
    assert captured["url"] == YandexOcrClient._URL
    assert captured["json"]["mimeType"] == "PDF"
    assert captured["json"]["languageCodes"] == ["ru"]
    assert captured["headers"]["Authorization"] == "Api-Key k1"
    assert captured["headers"]["x-folder-id"] == "f1"
    # content — валидный base64 исходных байт.
    import base64

    assert base64.b64decode(captured["json"]["content"]) == b"%PDF-1.7-raw-bytes"


def test_yandex_client_recognize_pdf_http_error_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = YandexOcrSettings(api_key="k1", folder_id="f1")
    client = YandexOcrClient(settings)

    def fake_post(url: str, json: dict, headers: dict) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(500, request=request, text="internal error")

    monkeypatch.setattr(client._client, "post", fake_post)
    assert client.recognize_pdf(b"raw") is None


def test_get_client_disabled_by_provider_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_mod, "OcrSettings", lambda: SimpleNamespace(provider="none"))
    assert ocr_mod.get_client() is None


def test_get_client_yandex_without_credentials_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_mod, "OcrSettings", lambda: SimpleNamespace(provider="yandex"))
    monkeypatch.setattr(
        ocr_mod, "YandexOcrSettings", lambda: SimpleNamespace(api_key=None, folder_id=None)
    )
    assert ocr_mod.get_client() is None


def test_get_client_yandex_configured_returns_cached_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocr_mod, "OcrSettings", lambda: SimpleNamespace(provider="yandex"))
    monkeypatch.setattr(
        ocr_mod,
        "YandexOcrSettings",
        lambda: YandexOcrSettings(api_key="k1", folder_id="f1"),
    )
    client1 = ocr_mod.get_client()
    client2 = ocr_mod.get_client()
    assert isinstance(client1, YandexOcrClient)
    assert client1 is client2  # синглтон — не пересоздаётся на каждый вызов


def test_set_client_overrides_and_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_mod, "OcrSettings", lambda: SimpleNamespace(provider="none"))

    class _Fake:
        def recognize_pdf(self, raw: bytes) -> str:
            return "fake"

    fake = _Fake()
    ocr_mod.set_client(fake)
    assert ocr_mod.get_client() is fake
    ocr_mod.set_client(None)
    # после сброса — снова считает по (замоканным) настройкам: provider=none.
    assert ocr_mod.get_client() is None
