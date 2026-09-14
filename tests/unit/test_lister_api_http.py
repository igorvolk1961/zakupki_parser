"""Unit-тесты request_json (lister/api/http.py) — ошибка несёт тело ответа.

Тело ответа при HTTP-ошибке (напр. 402/403) часто содержит реальную причину
(квота, требуется авторизация и т.п.) — раньше отбрасывалось, в логе оставался
только код статуса (см. известный случай mos.example 402 без объяснения).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from zakupki_parser.parser.lister.api.http import request_json


def _as_page(obj: object) -> Page:
    return cast(Page, obj)


class _FakeResponse:
    def __init__(self, ok: bool, status: int, text: str = "", json_value: Any = None) -> None:
        self.ok = ok
        self.status = status
        self._text = text
        self._json_value = json_value

    async def text(self) -> str:
        return self._text

    async def json(self) -> Any:
        return self._json_value


class _FakeRequest:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def get(self, url: str, timeout: int = 60000) -> _FakeResponse:
        return self._response

    async def post(
        self, url: str, data: dict[str, Any] | None = None, timeout: int = 60000
    ) -> _FakeResponse:
        return self._response


class _FakePage:
    def __init__(self, response: _FakeResponse) -> None:
        self.request = _FakeRequest(response)


async def test_error_message_includes_response_body() -> None:
    page = _FakePage(_FakeResponse(ok=False, status=402, text="Quota exceeded, retry after 24h"))
    with pytest.raises(RuntimeError, match="HTTP 402: Quota exceeded, retry after 24h"):
        await request_json(_as_page(page), "GET", "https://example.com/api", label="API деталей")


async def test_error_message_without_body_omits_colon() -> None:
    page = _FakePage(_FakeResponse(ok=False, status=500, text=""))
    with pytest.raises(RuntimeError, match=r"^API деталей вернул HTTP 500$"):
        await request_json(_as_page(page), "GET", "https://example.com/api", label="API деталей")


async def test_success_returns_json() -> None:
    page = _FakePage(_FakeResponse(ok=True, status=200, json_value={"a": 1}))
    result = await request_json(_as_page(page), "GET", "https://example.com/api")
    assert result == {"a": 1}
