"""Интеграционные тесты извлечения данных из HTML-фикстур."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from playwright.async_api import Page
from tests.conftest import load_fixture, set_html

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.detail import detail_files, extract_detail_vars
from zakupki_parser.parser.extractor import extract_from_scope

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_list_container_count(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("list_cardregion.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    containers = page.locator(platform.list_config.container)
    count = await containers.count()
    assert count > 0, "Должны находиться контейнеры записей в фикстуре списка"


@pytest.mark.asyncio
async def test_mos_container_hash_free(app_config: AppConfig, page: Page) -> None:
    """Селектор контейнера карточки mos.ru не содержит css-хеша styled-components (sc-…).

    Хешированные классы меняются при деплое — контейнер должен опираться на
    семантические классы Semantic UI (.ui.grid) и текст-якорь «(МСК)».
    """
    platform = app_config.dom.platforms["zakupki_mos"]
    container = platform.list_config.container
    assert "sc-" not in container, "Контейнер не должен использовать css-хеш styled-components"
    assert ".ui.grid" in container, "Контейнер должен опираться на семантический класс .ui.grid"
    assert "МСК" in container, "Контейнер должен использовать текст-якорь «(МСК)»"

    await set_html(page, load_fixture("list_cardregion.html"))
    containers = page.locator(container)
    count = await containers.count()
    assert count == 10, f"Должно быть 10 контейнеров карточек, найдено {count}"


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
    # Регресс: статус не должен быть обрезан CSS-усечением до '...' (text_content).
    assert "..." not in data.get("status", ""), "Статус не должен содержать CSS-обрезание '...'"
    assert data.get("nmck") is not None, "НМЦК должна извлекаться из карточки"
    assert data.get("law"), "Закон должен извлекаться из карточки"
    assert data.get("region"), "Регион должен извлекаться из карточки"


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
async def test_detail_files_expand_full_list(app_config: AppConfig, page: Page) -> None:
    """detail_files раскрывает «Смотреть все документы» и собирает все ссылки на файлы.

    Видимая часть списка — 2 файла; после клика по кнопке в DOM добавляются ещё 3
    (в т.ч. ТЗ). Без раскрытия они не попали бы в извлечённый список.
    """
    await set_html(page, load_fixture("detail_documents_expand.html"))
    platform = app_config.dom.platforms["zakupki_mos"]
    files = await detail_files(page, platform)

    names = [f["name"] for f in files]
    assert len(files) == 5, f"Должны быть собраны все файлы после раскрытия, получено: {names}"
    assert "ТЗ-полное.pdf" in names, "Файл из скрытой части списка должен попасть в результат"
    assert "Документ 3.docx" in names
    assert all("FileStorage/Download" in f["url"] for f in files)


@pytest.mark.asyncio
async def test_etpgpb_list_extraction(page: Page) -> None:
    """Верифицированные селекторы etpgpb (223-ФЗ) против реальной HTML-фикстуры."""
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["etpgpb_223"]
    await set_html(page, load_fixture("etpgpb_list.html"))

    containers = page.locator(platform.list_config.container)
    assert await containers.count() > 0, "Должны находиться карточки процедур etpgpb"

    data = await extract_from_scope(containers.first, platform.list_config.variables)
    assert data.get("number"), "Номер процедуры должен извлекаться"
    assert data.get("subject"), "Предмет должен извлекаться (productCard__title)"
    assert data.get("customer"), "Организатор должен извлекаться"
    assert data.get("status"), "Статус должен извлекаться (vTag--round)"
    assert data.get("purchase_type"), "Способ проведения должен извлекаться"
    assert data.get("nmck") is not None, "НМЦК должна извлекаться"
    assert isinstance(data.get("publication_date"), datetime)
    assert isinstance(data.get("deadline"), datetime)
    assert data["publication_date"] <= data["deadline"]


@pytest.mark.asyncio
async def test_etpgpb_detail_variables(page: Page) -> None:
    """Детальные поля etpgpb (статус/заказчик/ОКПД2) извлекаются с детальной страницы."""
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["etpgpb_223"]
    await set_html(page, load_fixture("etpgpb_detail.html"))

    data = await extract_detail_vars(page, platform)
    assert data.get("status"), "Текущий статус должен извлекаться с деталей"
    assert data.get("customer"), "Заказчик должен извлекаться с деталей"
    assert data.get("okpd2_code"), "Код ОКПД2 должен извлекаться с деталей"
    assert data.get("okpd2_name"), "Наименование ОКПД2 должно извлекаться с деталей"


@pytest.mark.asyncio
async def test_etpgpb_detail_files(page: Page) -> None:
    """Ссылки на файлы etpgpb (Документация) извлекаются с детальной страницы."""
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["etpgpb_223"]
    await set_html(page, load_fixture("etpgpb_detail.html"))

    files = await detail_files(page, platform)
    assert files, "Должны быть найдены файлы в секции Документация"
    assert all(f["name"] for f in files), "У каждого файла должно быть имя"
    assert all("file/get/" in f["url"] for f in files)


@pytest.mark.asyncio
async def test_etpgpb_customer_inn_from_org_page(page: Page) -> None:
    """ИНН заказчика etpgpb извлекается по селектору со страницы организации.

    На etpgpb ИНН — на странице организации (/catalog/customers/{slug}) в виде
    строки «ИНН: 6731033838» (метка customerInfo__label + значение). Точный
    селектор обязателен: обобщённый поиск по body ловит ИНН из рекламы
    («Банк ГПБ (АО) ИНН 7744001497»).
    """
    from zakupki_parser.parser.organization import extract_inn_from_text

    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["etpgpb_223"]
    org = platform.organization
    assert org is not None, "organization должен быть задан для etpgpb"
    selector = org.inn_page_selector
    assert selector, "inn_page_selector должен быть задан для etpgpb"

    await set_html(page, load_fixture("etpgpb_org.html"))
    locator = page.locator(selector).first
    assert await locator.count() > 0, "Селектор ИНН должен находиться на странице организации"
    text = await locator.text_content()
    assert extract_inn_from_text(text) == "6731033838"


@pytest.mark.asyncio
async def test_b2b_list_extraction(page: Page) -> None:
    """Верифицированные селекторы B2B-Center против реальной HTML-фикстуры."""
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["b2b_center"]
    await set_html(page, load_fixture("b2b_list.html"))

    containers = page.locator(platform.list_config.container)
    assert await containers.count() > 0, "Должны находиться строки таблицы B2B"

    data = await extract_from_scope(containers.first, platform.list_config.variables)
    assert data.get("number"), "Номер тендера должен извлекаться"
    assert data.get("subject"), "Предмет должен извлекаться"
    assert data.get("customer"), "Организатор должен извлекаться"
    assert isinstance(data.get("publication_date"), datetime)
    assert isinstance(data.get("deadline"), datetime)
    assert data["publication_date"] <= data["deadline"]


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


@pytest.mark.asyncio
async def test_eis_223_extraction(app_config: AppConfig, page: Page) -> None:
    await set_html(page, load_fixture("eis_list_223.html"))
    platform = app_config.dom.platforms["zakupki_gov"]
    containers = page.locator(platform.list_config.container)
    assert await containers.count() > 0
    data = await extract_from_scope(containers.first, platform.list_config.variables)
    # 223-ФЗ: 11-значный номер, закон, предмет из «Объект закупки», дедлайн
    assert len(data.get("number", "")) == 11, "Номер 223-ФЗ должен быть 11-значным"
    assert data.get("law") == "223-ФЗ"
    assert data.get("customer"), "Заказчик должен извлекаться (view223)"
    assert data.get("subject"), "Предмет 223-ФЗ должен извлекаться из body-value"
    assert data.get("deadline"), "Дедлайн (Окончание подачи) должен извлекаться"
