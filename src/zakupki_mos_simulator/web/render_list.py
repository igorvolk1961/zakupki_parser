"""Рендер страницы списка закупок (/purchase/list).

Воспроизводит DOM-структуру, заданную в ``demo_configs/config_dom.yaml`` для
площадки zakupki_mos: контейнеры карточек, переменные списка, сортировка и пагинация.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from zakupki_mos_simulator.config import DomSelectors
from zakupki_mos_simulator.data.format import format_money, parse_publication_date
from zakupki_mos_simulator.data.models import Procurement
from zakupki_mos_simulator.web.dom_classes import LABELED_VALUE_CLASS

PAGE_SIZE_DEFAULT = 10


def _pub_datetime(p: Procurement) -> datetime:
    """Дата публикации из строки «с ДД.ММ.ГГГГ …» (для сортировки по убыванию)."""
    parsed = parse_publication_date(p.publication_date)
    return parsed or datetime.min


def sorted_procurements(procurements: list[Procurement]) -> list[Procurement]:
    """Сортирует закупки по дате публикации по убыванию (как площадка)."""
    return sorted(procurements, key=_pub_datetime, reverse=True)


def _card(p: Procurement, selectors: DomSelectors) -> str:
    link = f"/need/{p.id}"
    price = format_money(p.nmck)
    # Ссылка на организацию (для извлечения ИНН, ADR-4).
    org_href = f"/companyProfile/customer/{p.customer_id}"
    subject = escape(p.subject)
    customer = escape(p.customer)
    purchase_type = escape(p.purchase_type)
    status = escape(p.status)
    number = escape(p.number)
    region = escape(p.region)
    law = escape(p.law)
    publication_date = escape(p.publication_date)
    return f"""<div>
  <div class="CardStyles__MainInfoContainer-sc-1rn3iq8-1">
    <div class="ui grid"><div class="row">
      <div class="column">
        <div class="CardStyles__FlexContainer-sc-1rn3iq8-0 CardStyles__MainInfoTopInfoContainer-sc-1rn3iq8-9">
          <div class="CardStyles__FlexContainer-sc-1rn3iq8-0">
            <div class="CardStyles__FlexContainer-sc-1rn3iq8-0 CardStyles__MainInfoTypeHeader-sc-1rn3iq8-3">
              <span>{purchase_type}</span>
            </div>
            <a class="ui header CardStyles__MainInfoNumberHeader-sc-1rn3iq8-6" href="{link}">
              <span class="EllipsedSpan__WordBreakSpan-sc-5i2ox1-0">{number}</span>
            </a>
          </div>
          <div id="state-indicator" class="ui green tiny header CardStyles__MainInfoStateIndicator-sc-1rn3iq8-7">
            <div class="content"><span class="EllipsedSpan__WordBreakSpan-sc-5i2ox1-0">{status}</span></div>
          </div>
        </div>
      </div>
    </div>
    <div class="row">
      <div class="column">
        <a class="ui header CardStyles__MainInfoNameHeader-sc-1rn3iq8-10" href="{link}">
          <span class="EllipsedSpan__WordBreakSpan-sc-5i2ox1-0">{subject}</span>
        </a>
      </div>
    </div>
    <div class="row">
      <div class="column">
        <a class="ui tiny header PurchaseCardStyles__MainInfoCustomerHeader-sc-xhk4mt-0" href="{org_href}">{customer}</a>
      </div>
    </div>
    <div class="row">
      <div class="column">
        <div class="{LABELED_VALUE_CLASS}">
          <label>Начальная цена</label>
          <div><div class="ui blue header CardStyles__PriceInfoNumber-sc-1rn3iq8-11">{price}</div></div>
        </div>
      </div>
    </div>
    <div class="row">
      <div class="column">
        <div class="CardStyles__AdditionalInfoContainer-sc-1rn3iq8-13">
          <div class="CardStyles__AdditionalInfoHeader-sc-1rn3iq8-14"><span>{region}</span></div>
          <div class="CardStyles__AdditionalInfoHeader-sc-1rn3iq8-14"><span>{law}</span></div>
          <div class="CardStyles__AdditionalInfoHeader-sc-1rn3iq8-14"><span>{publication_date}</span></div>
        </div>
      </div>
    </div>
    </div>
  </div>
</div>"""


def _sort_dropdown(selectors: DomSelectors) -> str:
    option = selectors.sort_option_text or "По дате публикации"
    container_class = (
        selectors.sort_dropdown or "SortDropdownStyles__SortDropdownContainer-sc-1j5g9d7-0"
    )
    # Извлекаем имена css-классов из селектора вида "div.Class .ui.dropdown".
    classes = " ".join(_css_classes(container_class))
    return f"""<div class="{classes}">
  <div class="ui dropdown">
    <div class="menu">
      <div class="item"><span class="text">{option}</span></div>
      <div class="item"><span class="text">По возрастанию цены</span></div>
      <div class="item"><span class="text">По убыванию цены</span></div>
    </div>
  </div>
</div>"""


def _css_classes(selector: str) -> list[str]:
    """Возвращает css-классы из CSS-селектора (теги/атрибуты игнорируются)."""
    import re

    result: list[str] = []
    for token in re.findall(r"[.#]?([A-Za-z][\w-]*)(?=\.| |\[|$)", selector):
        # Берём только хешированные классы styled-components (содержат "__").
        if "__" in token and token not in result:
            result.append(token)
    return result


def _pagination(page: int, total_pages: int) -> str:
    if page >= total_pages:
        return ""
    next_href = f"/purchase/list?page={page + 1}"
    return f"""<div class="ui pagination menu">
  <a type="nextItem" class="item" href="{next_href}">Далее</a>
</div>"""


def render_list(
    procurements: list[Procurement],
    selectors: DomSelectors,
    *,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> str:
    """Возвращает HTML страницы списка закупок."""
    ordered = sorted_procurements(procurements)
    total_pages = max(1, (len(ordered) + page_size - 1) // page_size)
    start = (page - 1) * page_size
    slice_items = ordered[start : start + page_size]
    cards = "".join(_card(p, selectors) for p in slice_items)
    dropdown = _sort_dropdown(selectors)
    pagination = _pagination(page, total_pages)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Реестр закупок — имитатор</title></head>
<body>
{dropdown}
<div class="PublicListStyles__PublicListContentContainer-sc-1epmhkd-1">
{cards}
</div>
{pagination}
</body></html>"""
