"""Общие фикстуры pytest для парсера."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

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
# Автозапуск мониторинга при старте сервиса в тестах отключён: тесты, поднимающие
# приложение на рабочих конфигах (create_app() без cfgdir), не должны запускать
# реальные циклы обхода. Отдельные тесты автозапуска переопределяют через YAML/env.
os.environ.setdefault("ZAKUPKI_AUTO_START_MONITORING", "false")


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
    os.environ.setdefault("ZAKUPKI_AUTO_START_MONITORING", "false")


@pytest.fixture(autouse=True, scope="session")
def _object_storage_in_memory() -> Iterator[None]:
    """Объектное хранилище в тестах — в памяти, не MinIO из .env разработчика.

    Хранилище обязательно (``scoring_common.object_storage``); без подмены тесты
    либо падали бы на проверке при старте, либо ходили бы в реальный MinIO.
    Уровень сессии: модульные фикстуры (``api_client`` поднимает приложение
    с проверкой хранилища при старте) создаются раньше функциональных.
    """
    from scoring_common import object_storage

    object_storage.use_in_memory()
    yield
    object_storage.set_client(None)


REPO_ROOT = Path(__file__).resolve().parents[1]
# Тесты грузят ВЫДЕЛЕННЫЙ тестовый набор конфигов (tests/configs), а не рабочие
# configs/* — чтобы результат не зависел от пользовательских настроек.
CONFIGS_DIR = REPO_ROOT / "tests" / "configs"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    # Сессионная фикстура создаётся лениво и может запускаться ПОСЛЕ интеграционных
    # тестов, которые снимают auth-переменные на teardown (os.environ.pop). Убеждаемся,
    # что дефолты на месте до загрузки конфига (иначе OpsConfig невалиден).
    os.environ.setdefault("ZAKUPKI_AUTH_SECRET", "test-secret")
    os.environ.setdefault("ZAKUPKI_INTERNAL_TOKEN", "internal-123")
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


class _StubSourceDriver:
    """Драйвер сбора сайтов для тестов: одна страница, без браузера и сети."""

    async def __aenter__(self) -> _StubSourceDriver:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def open(self, url: str) -> None:
        self._url = url

    async def text(self) -> str:
        return "тестовая страница"

    async def fingerprint(self) -> str:
        return "fp"

    async def current_url(self) -> str:
        return self._url

    async def find_next(self, page_no: int) -> None:
        return None

    async def click_next(self) -> None:
        return None

    async def scroll_to_bottom(self) -> None:
        return None

    async def wait_change(self, old_fingerprint: str, timeout_s: float) -> None:
        return None


@pytest.fixture(autouse=True, scope="session")
def _source_crawls_without_browser() -> Iterator[None]:
    """Сбор сайтов-источников в тестах API — без браузера и без сети.

    Приложение (``create_app``) иначе запускало бы Chromium и DNS-проверку для
    каждого ``website_url`` профиля. Тесты, которым нужен свой сценарий сбора,
    подменяют ``state.source_crawls`` сами.
    """
    from zakupki_parser.api import app as app_module
    from zakupki_parser.sources.manager import SourceCrawlManager

    def _build(state: Any) -> SourceCrawlManager:
        async def _any_url(url: str) -> None:
            return None

        return SourceCrawlManager(
            state.repository,
            state.cfg.service.site_sources,
            _StubSourceDriver,
            check_url=_any_url,
        )

    mp = pytest.MonkeyPatch()
    mp.setattr(app_module, "_build_source_crawls", _build)
    yield
    mp.undo()
