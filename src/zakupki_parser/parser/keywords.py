"""Клиентская проверка ключевых слов по тексту записи.

Используется для площадок (SPA), где серверный текстовый поиск в URL не отражается:
парсер отсекает записи, в предмете/номере которых нет ни одного из ключевых слов
``search_criteria.keywords`` (флаг ``list_config.post_filter_keywords``).
"""

from __future__ import annotations

from collections.abc import Iterable


def matches_any_keyword(text: str | None, keywords: Iterable[str]) -> bool:
    """Содержит ли ``text`` хотя бы одно ключевое слово (без учёта регистра).

    Пустой/незаданный список слов — фильтр не применяется (проходит всё), как и в
    описании ``SearchCriteria.keywords``.
    """
    if not keywords:
        return True
    if not text:
        return False
    low = text.lower()
    return any(k and k.lower() in low for k in keywords)
