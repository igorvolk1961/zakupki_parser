"""Тесты DOM-структуры имитатора: селекторы совпадают с demo-конфигом парсера."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from zakupki_mos_simulator.config import DomSelectors, load_dom_selectors
from zakupki_mos_simulator.llm.generate import build_demo_dataset
from zakupki_mos_simulator.settings import Settings
from zakupki_mos_simulator.web.app import SimulatorApp

COMPETENCIES = "тестовые компетенции"


@pytest.fixture()
def selectors() -> DomSelectors:
    return load_dom_selectors(Settings())


@pytest.fixture()
def client(selectors: DomSelectors) -> TestClient:
    dataset = build_demo_dataset(
        competencies=COMPETENCIES,
        okpd2_sections=["62", "63"],
        per_category=2,
    )
    sim = SimulatorApp(dataset=dataset, selectors=selectors)
    return TestClient(sim.app)


def _class_exists(html: str, css_class: str) -> bool:
    """Проверяет наличие css-класса в HTML как отдельного токена в атрибуте class."""
    return re.search(rf'[\s"\']{re.escape(css_class)}[\s"\']', html) is not None


def test_list_container_and_card_structure(client: TestClient, selectors: DomSelectors) -> None:
    html = client.get("/purchase/list").text
    # Контейнер списка и контейнер карточки.
    assert _class_exists(html, "PublicListStyles__PublicListContentContainer-sc-1epmhkd-1")
    assert _class_exists(html, "CardStyles__MainInfoContainer-sc-1rn3iq8-1")
    # Переменные списка: номер, тип, статус, предмет, заказчик, цена.
    assert _class_exists(html, "CardStyles__MainInfoNumberHeader-sc-1rn3iq8-6")
    assert _class_exists(html, "CardStyles__MainInfoTypeHeader-sc-1rn3iq8-3")
    assert _class_exists(html, "EllipsedSpan__WordBreakSpan-sc-5i2ox1-0")
    assert _class_exists(html, "PurchaseCardStyles__MainInfoCustomerHeader-sc-xhk4mt-0")
    assert _class_exists(html, "CardStyles__PriceInfoNumber-sc-1rn3iq8-11")
    # Доп. блок: регион/закон/даты.
    assert _class_exists(html, "CardStyles__AdditionalInfoContainer-sc-1rn3iq8-13")
    assert _class_exists(html, "CardStyles__AdditionalInfoHeader-sc-1rn3iq8-14")
    # Дата-строка и закон в формате обработчиков парсера.
    assert "(МСК)" in html
    assert "44-ФЗ" in html or "223-ФЗ" in html


def test_list_detail_link_points_to_need(client: TestClient) -> None:
    html = client.get("/purchase/list").text
    assert "/need/" in html


def test_list_sort_dropdown(client: TestClient, selectors: DomSelectors) -> None:
    html = client.get("/purchase/list").text
    assert _class_exists(html, "SortDropdownStyles__SortDropdownContainer-sc-1j5g9d7-0")
    assert "По дате публикации" in html


def test_list_pagination_last_page(client: TestClient) -> None:
    # Небольшая выборка => единственная страница => нет кнопки «Далее».
    html = client.get("/purchase/list").text
    assert "nextItem" not in html
    # При явной второй странице кнопка тоже отсутствует (end пагинации).
    html2 = client.get("/purchase/list?page=2").text
    assert "nextItem" not in html2


def test_detail_page_structure(client: TestClient) -> None:
    # Без dataset.id известны после генерации — возьмём из первого номера списка.
    list_html = client.get("/purchase/list").text
    import re

    m = re.search(r"/need/(\d+)", list_html)
    assert m is not None
    proc_id = int(m.group(1))
    html = client.get(f"/need/{proc_id}").text
    assert _class_exists(html, "LabeledValue-sc-1ftjcqo-0")
    for label in ("Заказчик", "Начальная цена", "Наименование ОКПД2", "Код ОКПД2"):
        assert label in html
    # Ссылка на организацию и файлы FileStorage/Download.
    assert "/companyProfile/customer/" in html
    assert "FileStorage/Download" in html


def test_org_page_inn(client: TestClient) -> None:
    html = client.get("/companyProfile/customer/900001").text
    assert _class_exists(html, "inn-value")
