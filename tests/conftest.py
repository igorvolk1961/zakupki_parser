"""Общие фикстуры pytest для парсера."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Page, async_playwright

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig

# Авторизация всегда включена (auth.enabled удалён): конфиги тестов требуют
# секрет и внутренний токен. Задаём значения по умолчанию для тестового окружения
# (конкретные тесты могут переопределить/снять их через monkeypatch/os.environ).
os.environ.setdefault("ZAKUPKI_AUTH_SECRET", "test-secret")
os.environ.setdefault("ZAKUPKI_INTERNAL_TOKEN", "internal-123")


@pytest.fixture(autouse=True)
def _auth_env_defaults() -> None:
    """Восстановить значения по умолчанию авторизации перед каждым тестом.

    Тесты интеграции с авторизацией снимают секрет/токен на teardown через
    ``os.environ.pop``; ``setdefault`` на уровне модуля выполняется один раз,
    поэтому «утечка» снятых переменных ломает последующие тесты (OpsConfig не
    валидируется без секрета). Перед каждым тестом снова выставляем дефолты.
    """
    os.environ.setdefault("ZAKUPKI_AUTH_SECRET", "test-secret")
    os.environ.setdefault("ZAKUPKI_INTERNAL_TOKEN", "internal-123")


REPO_ROOT = Path(__file__).resolve().parents[1]
# Тесты грузят ВЫДЕЛЕННЫЙ тестовый набор конфигов (tests/configs), а не рабочие
# configs/* — чтобы результат не зависел от пользовательских настроек.
CONFIGS_DIR = REPO_ROOT / "tests" / "configs"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)


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


def strip_scripts(html: str) -> str:
    """Убирает <script>…</script>: внешние скрипты в CI зависают и блокируют domcontentloaded."""
    return _SCRIPT_RE.sub("", html)


async def set_html(page: Page, html: str) -> None:
    await page.set_content(strip_scripts(html), wait_until="domcontentloaded")
