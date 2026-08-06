"""Рендер страницы организации (/companyProfile/customer/{id}) для извлечения ИНН."""

from __future__ import annotations

import re
from html import escape

from zakupki_mos_simulator.data.models import Customer
from zakupki_mos_simulator.web.dom_classes import LABELED_VALUE_CLASS

# Селектор ИНН по умолчанию (если не задан в demo_configs/config_dom.yaml).
DEFAULT_INN_SELECTOR = "div.inn-value"


def _class_name(selector: str | None) -> str:
    """Извлекает имя css-класса из селектора ИНН (например, 'div.inn-value')."""
    m = re.search(r"\.([A-Za-z][\w-]+)", selector or "")
    return m.group(1) if m else "inn-value"


def render_org(
    customer: Customer | None,
    customer_id: int,
    inn_selector: str | None = None,
) -> str:
    """HTML страницы организации: ИНН в элементе с классом из ``inn_selector``."""
    if customer is not None:
        name = escape(customer.name)
        inn = escape(customer.inn or "")
    else:
        name = f"Организация {customer_id}"
        inn = ""
    inn_class = _class_name(inn_selector or DEFAULT_INN_SELECTOR)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>{name}</title></head>
<body>
<div class="company-profile">
  <h1>{name}</h1>
  <div class="{LABELED_VALUE_CLASS}"><label>ИНН</label><div class="{inn_class}">{inn}</div></div>
</div>
</body></html>"""
