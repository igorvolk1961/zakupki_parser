"""Unit-тесты OCR-фолбэка для сканов PDF (scoring_common.ocr)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scoring_common import ocr as ocr_mod
from scoring_common.ocr import (
    ChainOcrClient,
    TesseractOcrClient,
    TesseractOcrSettings,
    YandexOcrClient,
    YandexOcrSettings,
    _text_from_yandex_response,
)


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


def test_get_client_yandex_without_credentials_falls_back_to_tesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет ключа/folder_id -> не None, а сразу локальный tesseract-клиент."""
    monkeypatch.setattr(ocr_mod, "OcrSettings", lambda: SimpleNamespace(provider="yandex"))
    monkeypatch.setattr(
        ocr_mod, "YandexOcrSettings", lambda: SimpleNamespace(api_key=None, folder_id=None)
    )
    client = ocr_mod.get_client()
    assert isinstance(client, TesseractOcrClient)


def test_get_client_yandex_configured_returns_chain_with_tesseract_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ключ/folder_id заданы -> цепочка Yandex -> tesseract (не голый YandexOcrClient)."""
    monkeypatch.setattr(ocr_mod, "OcrSettings", lambda: SimpleNamespace(provider="yandex"))
    monkeypatch.setattr(
        ocr_mod,
        "YandexOcrSettings",
        lambda: YandexOcrSettings(api_key="k1", folder_id="f1"),
    )
    client = ocr_mod.get_client()
    assert isinstance(client, ChainOcrClient)
    assert isinstance(client._providers[0], YandexOcrClient)
    assert isinstance(client._providers[1], TesseractOcrClient)


def test_get_client_provider_tesseract_skips_yandex_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocr_mod, "OcrSettings", lambda: SimpleNamespace(provider="tesseract"))
    client = ocr_mod.get_client()
    assert isinstance(client, TesseractOcrClient)


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
    assert isinstance(client1, ChainOcrClient)
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


def test_tesseract_client_no_binary_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_mod.shutil, "which", lambda _name: None)
    client = TesseractOcrClient()
    assert client.recognize_pdf(b"raw") is None


def test_tesseract_client_recognizes_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_mod.shutil, "which", lambda _name: "/usr/bin/tesseract")

    class _FakePage:
        def to_image(self, resolution: int) -> Any:
            return SimpleNamespace(original="fake-image")

    class _FakePdf:
        def __enter__(self) -> Any:
            return SimpleNamespace(pages=[_FakePage(), _FakePage()])

        def __exit__(self, *exc: Any) -> None:
            return None

    fake_pdfplumber = SimpleNamespace(open=lambda _stream: _FakePdf())
    calls: list[Any] = []

    def fake_image_to_string(image: Any, lang: str) -> str:
        calls.append((image, lang))
        return "распознанный текст"

    fake_pytesseract = SimpleNamespace(image_to_string=fake_image_to_string)
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)

    client = TesseractOcrClient(TesseractOcrSettings(languages="rus+eng", resolution=200))
    text = client.recognize_pdf(b"%PDF-raw")
    assert text == "распознанный текст\n\nраспознанный текст"
    assert len(calls) == 2
    assert calls[0][1] == "rus+eng"


def test_tesseract_client_missing_dependency_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_mod.shutil, "which", lambda _name: "/usr/bin/tesseract")
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    client = TesseractOcrClient()
    assert client.recognize_pdf(b"raw") is None


def test_chain_client_tries_providers_in_order_until_non_empty() -> None:
    class _Empty:
        def recognize_pdf(self, raw: bytes) -> str | None:
            return None

    class _Fallback:
        def recognize_pdf(self, raw: bytes) -> str | None:
            return "fallback-text"

    chain = ChainOcrClient([_Empty(), _Fallback()])
    assert chain.recognize_pdf(b"raw") == "fallback-text"


def test_chain_client_all_empty_returns_none() -> None:
    class _Empty:
        def recognize_pdf(self, raw: bytes) -> str | None:
            return None

    chain = ChainOcrClient([_Empty(), _Empty()])
    assert chain.recognize_pdf(b"raw") is None
