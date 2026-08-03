"""Менеджер браузера Playwright с антиблок-мерами.

Запускает Chromium, применяет stealth-скрипты, сохраняет сессию (куки) между
запусками и обеспечивает вежливые задержки.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.browser.stealth import apply_init_scripts
from zakupki_parser.config.models import BrowserConfig

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class BrowserManager:
    """Обёртка над запуском/остановкой браузера и контекста."""

    def __init__(self, cfg: BrowserConfig, base_dir: Path | None = None) -> None:
        self._cfg = cfg
        self._base_dir = base_dir or Path(".")
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._playwright: Playwright | None = None
        self.delayer = Delayer(cfg)

    @property
    def session_dir(self) -> Path:
        return (self._base_dir / self._cfg.session_dir).resolve()

    async def start(self) -> None:
        if self._browser is not None:
            return
        pw = await async_playwright().start()
        self._playwright = pw
        launch_args: list[str] = [
            "--disable-blink-features=AutomationControlled",
        ]
        launch_kwargs: dict[str, Any] = {
            "headless": self._cfg.headless,
            "args": launch_args,
        }
        if self._cfg.chromium_executable_path:
            launch_kwargs["executable_path"] = self._cfg.chromium_executable_path
        self._browser = await pw.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {
            "locale": self._cfg.locale,
            "timezone_id": self._cfg.timezone,
            "viewport": {
                "width": self._cfg.viewport_width,
                "height": self._cfg.viewport_height,
            },
            "user_agent": self._cfg.user_agent or DEFAULT_UA,
        }
        if self._cfg.persist_session:
            storage_path = self.session_dir / "storage.json"
            if storage_path.is_file():
                context_kwargs["storage_state"] = str(storage_path)

        self._context = await self._browser.new_context(**context_kwargs)
        await apply_init_scripts(self._context, self._cfg.disable_webdriver_flag)
        await self.delayer.sleep()

    async def new_page(self) -> Page:
        if self._context is None:
            raise RuntimeError("Контекст браузера не инициализирован")
        return await self._context.new_page()

    async def save_session(self) -> None:
        if self._context is not None and self._cfg.persist_session:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            await self._context.storage_state(path=str(self.session_dir / "storage.json"))

    async def close(self) -> None:
        if self._context is not None:
            with suppress(PlaywrightError):
                await self._context.close()
        if self._browser is not None:
            with suppress(PlaywrightError):
                await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._context = None
        self._browser = None
        self._playwright = None
