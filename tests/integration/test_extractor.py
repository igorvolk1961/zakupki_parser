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

    assert data.get("number"), "Номер закупки должен извлекаться из карточки"
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
async def test_roseltorg_detail_files(app_config: AppConfig, page: Page) -> None:
    """roseltorg: приложения к извещению берутся из блока #documents.

    Регресс: конфиг раньше имел detail.files: [] и файлы не извлекались, хотя
    ссылки <a class="lot-docs__file" href="https://business.roseltorg.ru/...">
    присутствуют в HTML. Проверяем имена и абсолютные URL (поддомен business.ru
    сохраняется как есть).
    """
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["roseltorg_223fz"]
    await set_html(page, load_fixture("roseltorg_detail.html"))
    files = await detail_files(page, platform)

    assert files, "Должны быть найдены приложения к извещению (detail.files)"
    names = [f["name"] for f in files]
    assert "Мобильное_приложение_с_виртуальным_компаньоном 03.08.docx" in names
    assert "356_МЦ 03.08.docx" in names
    assert "[B0308261802236] Лот №1" in names
    # Все ссылки абсолютные на поддомен business.roseltorg.ru — не должны обрезаться.
    prefix = "https://business.roseltorg.ru/api/v1/documents/"
    assert all(f["url"].startswith(prefix) for f in files)
    assert all(f["name"] for f in files), "У каждого файла должно быть имя"


@pytest.mark.asyncio
async def test_fabrikant_commercial_documentation_files(page: Page) -> None:
    """fabrikant (коммерческие): файлы на вкладке «Документация», имя и URL разделены.

    Регресс: detail.files раньше искал только EIS-ссылки (44-ФЗ), а коммерческие
    процедуры держат документы на /v2/trades/procedure/documentation/<id> и в другой
    разметке (имя в td.procedure-document-file span, URL в td.action a.download).
    Проверяем извлечение по новым name_selector/url_selector.
    """
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["fabrikant"]
    await set_html(page, load_fixture("fabrikant_documentation.html"))
    files = await detail_files(page, platform)

    names = [f["name"] for f in files]
    expected = {
        "Анкета_претендента.xlsx",
        "Заявление_о_добросовестности_контрагента.docx",
        "Коммерческое_предложение.docx",
        "Критерии_оценки.docx",
        "Соглашение_о_конфиденциальности.doc",
        "Техническое_задание.docx",
        "Требования_к_участникам.docx",
    }
    assert len(files) == 7, f"Ожидалось 7 документов, получено: {names}"
    assert expected.issubset(set(names)), f"Не найдены: {expected - set(names)}"
    prefix = "https://fabrikant.ru/v2/trades/procedure/documentation/download/single/"
    assert all(f["url"].startswith(prefix) for f in files)


@pytest.mark.asyncio
async def test_etpgpb_list_extraction(page: Page) -> None:
    """Верифицированные селекторы etpgpb против реальной HTML-фикстуры."""
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["etpgpb"]
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
async def test_eis_223_documents_files(app_config: AppConfig, page: Page) -> None:
    """223-ФЗ: файлы на notice223/documents.html, доступны по вкладке «Документы».

    Регресс: у 223-ФЗ не был задан ни files_page, ни files_page_link — парсер оставался
    на common-info.html (там файлов нет) и не извлекал приложения. Подстраницы 223-ФЗ
    не принимают только regNumber (400/500), поэтому переход выполняется по href
    вкладки (files_page_link), несущей purchaseNoticeNumber/noticeGuid. Здесь проверено
    извлечение 5 файлов a[href*='filestore/public/'] (223/filestore ...).
    """
    await set_html(page, load_fixture("eis_223_documents.html"))
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["zakupki_gov_223fz"]
    files = await detail_files(page, platform)
    assert files, "Должны быть найдены файлы на notice223/documents.html"
    assert len(files) == 5, f"Ожидалось 5 файлов, получено: {len(files)}"
    assert all(f["name"] for f in files), "У каждого файла должно быть имя"
    assert all(f["url"].startswith("https://zakupki.gov.ru/223/filestore/public/") for f in files)


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


@pytest.mark.asyncio
async def test_fabrikant_44_region_delivery_place(page: Page) -> None:
    """fabrikant 44-ФЗ: регион — место поставки (таблица «Субъект/…/Место поставки»).

    Структура взята из полного SSR-рендера детальной страницы (2026-09-04):
    регион в начале значения «Место поставки» первой строки («обл. Челябинская, …»),
    колонка «Субъект» при этом пуста.
    """
    await set_html(page, load_fixture("fabrikant_delivery_place.html"))
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["fabrikant"]
    region_var = next(v for v in platform.detail.variables if v.name == "region")
    data = await extract_from_scope(page.locator("body"), [region_var])
    region = (data.get("region") or "").strip()
    assert region, "Регион должен извлекаться из таблицы мест поставки"
    assert "Челябинск" in region, f"Ожидался регион с «Челябинск», получено: {region!r}"


@pytest.mark.asyncio
async def test_gz_lot_online_region_dom_delivery_place(page: Page) -> None:
    """gz (lot-online 44): регион из DOM по требованию — поле «Место поставки».

    В API (реестр и lotInfo JSON-RPC) региона нет; «Место поставки» живёт на
    common-странице (app-info: label «Место поставки» -> div.form-control-static > p).
    Открывается только по явному региональному запросу профиля (region_on_demand_dom).
    """
    await set_html(page, load_fixture("gz_common_place.html"))
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["lot_online_44"]
    assert platform.detail.region_on_demand_dom is True
    data = await extract_detail_vars(page, platform)
    region = (data.get("region") or "").strip()
    assert region, "Регион должен извлекаться из «Место поставки»"
    assert "Ярославль" in region, f"Ожидался регион с «Ярославль», получено: {region!r}"


@pytest.mark.asyncio
async def test_roseltorg_region_from_search_card(page: Page) -> None:
    """roseltorg: регион заказчика из карточки выдачи (.search-results__region).

    Место поставки на деталях roseltorg не показывается — регион заказчика
    структурирован в карточке поиска: «NN. г. Москва» (код субъекта отрезается).
    """
    await set_html(page, load_fixture("roseltorg_card_region.html"))
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["roseltorg_223fz"]
    region_var = next(v for v in platform.list_config.variables if v.name == "region")
    data = await extract_from_scope(page.locator("body"), [region_var])
    region = (data.get("region") or "").strip()
    assert region, "Регион должен извлекаться из карточки выдачи"
    assert region == "г. Москва", f"Ожидался «г. Москва», получено: {region!r}"


@pytest.mark.asyncio
async def test_b2b_center_region_from_delivery_address(page: Page) -> None:
    """b2b-center: регион из «Адрес поставки / оказания услуг» (SSR market-next).

    Блок .delivery-address-list (значение в .collapsable-items-list-item p) виден
    анонимно; содержит регион/населённый пункт («г. Санкт-Петербург, поселок
    Понтонный…»). Документация/контакты — только зарегистрированным.
    """
    await set_html(page, load_fixture("b2b_delivery_address.html"))
    cfg = load_config(REPO_ROOT / "configs")
    platform = cfg.dom.platforms["b2b_center"]
    data = await extract_detail_vars(page, platform)
    region = (data.get("region") or "").strip()
    assert region, "Регион должен извлекаться из адреса поставки"
    # Сырой адрес: «Санкт - Петербург» (пробелы вокруг дефиса) — проверяем слова.
    assert "Санкт" in region, f"Ожидался адрес с «Санкт», получено: {region!r}"
    assert "Петербург" in region, f"Ожидался адрес с «Петербург», получено: {region!r}"
    assert "Понтонный" in region, f"Ожидался адрес с «Понтонный», получено: {region!r}"
