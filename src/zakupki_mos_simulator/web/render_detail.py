"""Рендер детальной страницы закупки (/need/{id})."""

from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from zakupki_mos_simulator.data.format import format_money
from zakupki_mos_simulator.data.models import FileMeta, Procurement
from zakupki_mos_simulator.web.dom_classes import LABELED_VALUE_CLASS


def _labeled_value(label: str, value: str) -> str:
    return f'<div class="{LABELED_VALUE_CLASS}"><label>{label}</label><div>{value}</div></div>'


def _safe_file_url(url: str) -> bool:
    """Разрешает только относительные (без host) и http(s) URL для файлов.

    Протокол-относительные URL (``//host/...``) считаются небезопасными: несмотря
    на пустую схему, они разрешаются во внешний хост.
    """
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") or (parts.scheme == "" and not parts.netloc)


def _file_link(f: FileMeta) -> str:
    if not _safe_file_url(f.url):
        return f'<div><i class="blue file icon"></i><span>{escape(f.name)}</span></div>'
    return (
        f'<div><i class="blue file icon"></i>'
        f'<a href="{escape(f.url, quote=True)}">{escape(f.name)}</a></div>'
    )


def render_detail(p: Procurement) -> str:
    """HTML детальной страницы: LabeledValue-блоки + файлы + ссылка на организацию."""
    customer = escape(p.customer)
    number = escape(p.number)
    subject = escape(p.subject)
    okpd2_name = escape(p.okpd2_name)
    okpd2_code = escape(p.okpd2_code)
    publication_date = escape(p.publication_date)
    customer_html = (
        f'<a target="_blank" href="/companyProfile/customer/{p.customer_id}">{customer}</a>'
    )
    files_html = "".join(_file_link(f) for f in p.files)
    blocks = [
        _labeled_value("Заказчик", customer_html),
        _labeled_value("Начальная цена", format_money(p.nmck)),
        _labeled_value("Наименование ОКПД2", okpd2_name),
        _labeled_value("Код ОКПД2", okpd2_code),
        _labeled_value("Сроки", publication_date),
        _labeled_value("Документы", files_html),
    ]
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Закупка {number}</title></head>
<body>
<div class="ProcedurePageLayoutStyles__MainInfoContainer-sc-72qxt5-0">
  <h1>Закупка по потребностям <b>{number}</b></h1>
  <h2>{subject}</h2>
  {"".join(blocks)}
</div>
</body></html>"""
