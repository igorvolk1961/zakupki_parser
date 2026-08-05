"""Интеграционные тесты извлечения данных из HTML-фикстур."""

from __future__ import annotations

from datetime import datetime

import pytest
from playwright.async_api import Page
from tests.conftest import load_fixture, set_html

from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.detail import detail_files, extract_detail_vars
from zakupki_parser.parser.extractor import extract_from_scope


@pytest.mark.asyncio
async def test_list_container_count(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list_config.container)
    count = await containers.count()
    assert count > 0, "Должны находиться контейнеры записей в фикстуре списка"


@pytest.mark.asyncio
async def test_extract_list_variables(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list_config.container)
    data = await extract_from_scope(containers.first, platform.list_config.variables)

    assert data.get("number"), "Номер заявки должен извлекаться из карточки"
    assert data.get("customer"), "Заказчик должен извлекаться из карточки"
    assert data.get("subject"), "Предмет должен извлекаться из карточки"
    assert data.get("status"), "Статус должен извлекаться из карточки"
    assert data.get("nmck") is not None, "НМЦК должна извлекаться из карточки"
    assert data.get("law"), "Закон должен извлекаться из карточки"
    assert data.get("region"), "Регион должен извлекаться из карточки"
    assert "с " in (data.get("dates") or ""), "Строка дат должна извлекаться"


@pytest.mark.asyncio
async def test_publication_date_and_deadline(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list_config.container)
    data = await extract_from_scope(containers.first, platform.list_config.variables)
    pub = data.get("publication_date")
    dl = data.get("deadline")
    assert isinstance(pub, datetime), "Дата публикации должна быть datetime"
    assert isinstance(dl, datetime), "Срок подачи должен быть datetime"
    assert pub <= dl


@pytest.mark.asyncio
async def test_detail_link_present(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list_config.container)
    link = containers.first.locator(platform.list_config.detail_link).first
    href = await link.get_attribute("href")
    assert href and "/need/" in href


@pytest.mark.asyncio
async def test_detail_variables(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("detail_content.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    data = await extract_detail_vars(page, platform)
    assert data.get("customer"), "Заказчик должен извлекаться с деталей"
    assert data.get("nmck") is not None, "НМЦК должна извлекаться с деталей"
    assert data.get("okpd2_code"), "Код ОКПД2 должен извлекаться с деталей"


@pytest.mark.asyncio
async def test_detail_files(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("detail_content.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    files = await detail_files(page, platform)
    assert files, "Должны быть найдены ссылки на файлы"
    assert all(f["name"] for f in files), "У каждого файла должно быть имя"
    assert all("FileStorage/Download" in f["url"] for f in files)


@pytest.mark.asyncio
async def test_eis_list_extraction(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("eis_list.html"))
    platform = app_config.dom.platforms["zakupki_gov"]
    containers = page.locator(platform.list_config.container)
    count = await containers.count()
    assert count > 0, "Должны находиться карточки ЕИС"

    data = await extract_from_scope(containers.first, platform.list_config.variables)
    assert data.get("number"), "Реестровый номер должен извлекаться"
    assert data.get("customer"), "Заказчик должен извлекаться"
    assert data.get("nmck") is not None, "НМЦК должна извлекаться"
    assert data.get("law") in ("44-ФЗ", "223-ФЗ", None)
    assert data.get("update_date"), "Дата обновления должна извлекаться"


@pytest.mark.asyncio
async def test_eis_detail_link(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("eis_list.html"))
    platform = app_config.dom.platforms["zakupki_gov"]
    containers = page.locator(platform.list_config.container)
    link = containers.first.locator(platform.list_config.detail_link).first
    href = await link.get_attribute("href")
    assert href and "common-info.html" in href


@pytest.mark.asyncio
async def test_eis_documents_files(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("eis_documents.html"))
    platform = app_config.dom.platforms["zakupki_gov"]
    files = await detail_files(page, platform)
    assert files, "Должны быть найдены файлы на documents.html ЕИС"
    assert all(f["name"] for f in files), "У каждого файла должно быть имя"
    assert all("filestore/public/" in f["url"] for f in files)
