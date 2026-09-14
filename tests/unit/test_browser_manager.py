"""Unit-тесты детектора краша браузера (BrowserManager._on_browser_disconnected).

Единственный на процесс браузер Playwright общий для всех одновременных обходов
площадок (``max_concurrent_platforms``) — его неожиданное отключение (креш/
зависание Chromium) останавливает их все разом. Отличаем это от штатного
``close()`` через флаг ``_closing`` — тесты проверяют оба случая.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from playwright.async_api import Browser

from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.config.models import BrowserConfig


def _manager() -> BrowserManager:
    return BrowserManager(BrowserConfig())


def _as_browser(obj: object) -> Browser:
    return cast(Browser, obj)


def test_unexpected_disconnect_logs_critical(caplog: Any) -> None:
    mgr = _manager()
    with caplog.at_level(logging.INFO):
        mgr._on_browser_disconnected(_as_browser(None))  # noqa: SLF001
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert "неожиданно отключился" in critical[0].message


def test_expected_close_does_not_log_critical(caplog: Any) -> None:
    mgr = _manager()
    mgr._closing = True  # noqa: SLF001
    with caplog.at_level(logging.INFO):
        mgr._on_browser_disconnected(_as_browser(None))  # noqa: SLF001
    assert not any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert any("штатное" in r.message for r in caplog.records)


async def test_close_sets_and_resets_closing_flag() -> None:
    """close() без запущенного браузера — _closing выставляется и сбрасывается,
    не оставляя менеджер в состоянии «глушим будущие креши» навсегда."""
    mgr = _manager()
    await mgr.close()
    assert mgr._closing is False  # noqa: SLF001
