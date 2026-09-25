"""Обработка текста собранных страниц сайта."""

from __future__ import annotations


def strip_common_edges(pages: list[str], min_pages: int = 3) -> list[str]:
    """Убирает общие для ВСЕХ страниц строки в начале и в конце (шапка, меню,
    панель фильтров, подвал) — это оформление сайта, а не данные.

    Удаляется только непрерывный блок совпадающих строк от начала и от конца
    страницы: одинаковые строки ВНУТРИ данных («Сбор (1)» в каждой строке
    таблицы) не трогаются. Меньше ``min_pages`` страниц — общее оформление
    от данных не отличить, текст не меняется. Страница целиком не удаляется:
    если бы от неё ничего не осталось, она остаётся как есть.
    """
    if len(pages) < min_pages:
        return pages
    lines = [p.split("\n") for p in pages]
    shortest = min(len(ls) for ls in lines)
    head = 0
    while head < shortest and all(ls[head] == lines[0][head] for ls in lines):
        head += 1
    tail = 0
    while tail < shortest - head and all(ls[-1 - tail] == lines[0][-1 - tail] for ls in lines):
        tail += 1
    result = []
    for page, ls in zip(pages, lines, strict=True):
        body = ls[head : len(ls) - tail]
        result.append("\n".join(body).strip("\n") if any(s.strip() for s in body) else page)
    return result
