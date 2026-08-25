"""Общие HTTP-хелперы извлечения деталей через открытые API площадок."""

from __future__ import annotations

from typing import Any, cast

from playwright.async_api import Page

from zakupki_parser.parser.lister.api import request_json


async def _post_json(page: Page, url: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST JSON-RPC запрос и разбор JSON-ответа (сбой — исключение для retry)."""
    return cast(
        dict[str, Any], await request_json(page, "POST", url, body=body, label="API деталей")
    )


async def _get_json(page: Page, url: str) -> dict[str, Any]:
    """GET JSON-запрос (сбой — исключение для retry)."""
    return cast(dict[str, Any], await request_json(page, "GET", url, label="API деталей"))
