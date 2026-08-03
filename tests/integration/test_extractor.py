"""Интеграционные тесты извлечения данных из HTML-фикстур."""

from __future__ import annotations

import pytest
from playwright.async_api import Page
from tests.conftest import load_fixture, set_html

from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.extractor import extract_from_scope


@pytest.mark.asyncio
async def test_list_container_count(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list.container)
    count = await containers.count()
    assert count > 0, "Должны находиться контейнеры записей в фикстуре списка"


@pytest.mark.asyncio
async def test_extract_list_variables(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list.container)
    first = containers.first
    data = await extract_from_scope(first, platform.list.variables)
    assert data.get("number"), "Номер заявки должен извлекаться из карточки"
    assert data.get("customer"), "Заказчик должен извлекаться из карточки"


@pytest.mark.asyncio
async def test_detail_link_present(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list.container)
    link = containers.first.locator(platform.list.detail_link).first
    href = await link.get_attribute("href")
    assert href and "/need/" in href
