"""HTTP-запрос к API площадки через браузер (page.request) и разбор JSON."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page


async def request_json(
    page: Page,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    label: str = "API",
) -> Any:
    """HTTP-запрос к API площадки и разбор JSON-ответа.

    Сбой сети/HTTP/структуры поднимает RuntimeError — вызывающий ретраит через
    ``run_with_retry`` (общий контракт ошибок списка и деталей).
    """
    if method == "GET":
        resp = await page.request.get(url, timeout=60000)
    elif method == "POST":
        resp = await page.request.post(url, data=body, timeout=60000)
    else:
        raise ValueError(f"Неизвестный метод запроса: {method}")
    if not resp.ok:
        raise RuntimeError(f"{label} вернул HTTP {resp.status}")
    try:
        return await resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{label}: некорректный JSON: {exc}") from exc
