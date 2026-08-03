"""Общие фикстуры pytest для парсера."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Page, async_playwright

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = REPO_ROOT / "configs"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    return load_config(CONFIGS_DIR)


@pytest_asyncio.fixture
async def page() -> AsyncIterator[Page]:
    """Запускает реальный Chromium и отдаёт страницу (для фикстур)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(locale="ru-RU")
        pg = await context.new_page()
        yield pg
        await browser.close()


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


async def set_html(page: Page, html: str) -> None:
    await page.set_content(html, wait_until="domcontentloaded")
