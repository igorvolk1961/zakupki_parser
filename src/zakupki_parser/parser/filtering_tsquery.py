"""Транслятор DSL ключевых слов (``parser/filtering.py``) в синтаксис Postgres tsquery.

Нужен для индексного пути «горячего» пересбора (проиндексированный диапазон ОКПД2,
``storage/db/search_index.py``): ``search_tsv`` строится через ``to_tsvector('simple', …)``,
матчинг — через ``search_tsv @@ to_tsquery('simple', …)``.

Конфигурация **'simple'**, а не 'russian', выбрана намеренно: DSL (``filtering.py``) не
делает лингвистический стемминг — только СИМВОЛЬНОЕ усечение (``слов*`` -> префикс
``слов``, любое окончание) либо точное слово (границы слова, регистронезависимо).
'russian' применил бы морфологический стемминг Postgres (снятие окончаний по словарю)
и разошёлся бы с этой семантикой; 'simple' (нижний регистр + токенизация без
стемминга) сохраняет точное символьное соответствие.

Семантика DSL, которую транслятор обязан воспроизвести (см. ``filtering.py``):
- стем-токен ``слов*`` -> префиксный лексемный матч (``слов:*``);
- точный токен ``слово`` -> точная лексема (регистр уже приводится ``to_tsvector``);
- «фраза» из нескольких токенов БЕЗ ``~N`` — это НЕ проверка смежности/порядка слов,
  а конъюнкция независимых совпадений каждого токена ГДЕ УГОДНО в тексте (см.
  ``_expression_match``: ``all(_token_match(...) for token in tokens)``) -> ``&``;
- проксимити ``(a* b*)~N`` — «не более N слов МЕЖДУ токенами» (Lucene-slack) ->
  для каждой соседней пары токенов расстояние (в терминах позиций лексем tsquery)
  от 1 (0 слов между, ``<1>``/``<->``) до N+1 (N слов между, ``<N+1>``) -> OR по
  расстояниям, AND между парами.

**Точная параллель с Python-движком НЕ гарантирована** (два независимых движка на
разных данных: regex по ``subject``/тексту документов vs tsvector-токенизация
Postgres) — остаточное расхождение в токенизации (например, вокруг дефисов/цифр)
возможно; см. риск в плане индексации. Тестами (``tests/unit/test_filtering_tsquery.py``)
покрыты представительные кейсы обоих путей.
"""

from __future__ import annotations

import re

# Совпадает с zakupki_parser.parser.filtering._PROXIMITY_RE (не импортируется —
# private-символ другого модуля, оставляем модуль самодостаточным).
_PROXIMITY_RE = re.compile(r"^\((.+)\)~(\d+)$")

# Конфигурация Postgres text search, под которую построен транслятор (см. докстринг
# модуля) — используется вызывающим кодом при построении ``to_tsvector``/``to_tsquery``.
TS_CONFIG = "simple"


def _tsquery_lexeme(token: str) -> str:
    """Один токен DSL -> лексема tsquery (квотирована — токен может содержать дефис)."""
    if token.endswith("*"):
        stem = token[:-1]
        return f"'{stem}':*"
    return f"'{token}'"


def _tsquery_proximity(inner: str, distance: int) -> str | None:
    """Проксимити-группа ``(a* b* c*)~N`` -> цепочка расстояний между соседними токенами.

    Возвращает ``None`` для вырожденного случая (нет токенов внутри группы) —
    как и Python ``_proximity_match``, у которого пустой список токенов не матчит
    ничего; вызывающий код должен трактовать ``None`` как «выражение никогда не
    совпадёт» (не включать в OR-объединение позитивных слов).
    """
    tokens = [t for t in inner.split() if t]
    if not tokens:
        return None
    lexemes = [_tsquery_lexeme(t) for t in tokens]
    if len(lexemes) == 1:
        return lexemes[0]
    pair_terms = []
    for left, right in zip(lexemes, lexemes[1:], strict=False):
        # "не более N слов между" -> позиционное расстояние от 1 (0 слов между,
        # <1> он же <->) до N+1 (N слов между) включительно.
        distances = " | ".join(f"{left} <{d}> {right}" for d in range(1, distance + 2))
        pair_terms.append(f"({distances})")
    return " & ".join(pair_terms)


def compile_keyword_to_tsquery(expression: str) -> str | None:
    """Одно выражение DSL (стем/фраза/проксимити) -> текст tsquery (без ``to_tsquery(...)``).

    ``None`` — выражение вырождено и никогда не совпадёт (см. ``_tsquery_proximity``).
    """
    prox = _PROXIMITY_RE.match(expression)
    if prox:
        return _tsquery_proximity(prox.group(1), int(prox.group(2)))
    tokens = expression.split()
    if not tokens:
        return None
    if len(tokens) == 1:
        return _tsquery_lexeme(tokens[0])
    # «Фраза» без ~N — конъюнкция независимых токенов (см. докстринг модуля),
    # НЕ проверка смежности/порядка.
    return " & ".join(_tsquery_lexeme(t) for t in tokens)


def compile_keywords_to_tsquery(keywords: list[str]) -> str | None:
    """Список позитивных ключевых слов (хотя бы одно совпало) -> объединённый tsquery.

    ``None`` — пустой список (по семантике R9 «фильтра нет» — вызывающий код НЕ должен
    добавлять условие ``@@`` в WHERE, а не трактовать как «никогда не совпадёт») ЛИБО
    все выражения списка вырождены (после фильтрации ничего не осталось).
    """
    if not keywords:
        return None
    parts = [p for p in (compile_keyword_to_tsquery(kw) for kw in keywords) if p]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return " | ".join(f"({p})" for p in parts)
