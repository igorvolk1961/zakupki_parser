"""Клиентская пост-фильтрация закупок по ключевым словам профиля (R9).

Выражения приходят из таблицы ``keywords`` (канонический источник; сид —
файл-сид профиля, см. ``Profile.keywords_rel``) и сохраняют исходный синтаксис:
- ``слов*`` — слово с усечением (стеб-префикс, регистронезависимо);
- ``(фраза* фраза*)~N`` — не более N слов между токенами (проксимити);
- ``точная фраза`` / ``"фраза"`` — точное совпадение (по границам слова для
  одиночного слова).

Позитивные слова: закупка проходит, если совпало хотя бы одно (пустой список —
фильтра нет). Слова-исключения: любое совпадение отбрасывает закупку.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

_WORD_RE = re.compile(r"[\wа-яёА-ЯЁ-]+", re.UNICODE)
_PROXIMITY_RE = re.compile(r"^\((.+)\)~(\d+)$")


def _subject(record: dict[str, Any]) -> str:
    value = record.get("subject")
    if isinstance(value, str):
        return value
    detail = record.get("detail_json") or {}
    subject = detail.get("subject")
    return subject if isinstance(subject, str) else ""


def _stem_pattern(token: str) -> str:
    """Паттерн усечённого слова: ``услуг*`` -> ``услуг[а-яё]*`` (граница слова)."""
    stem = token.rstrip("*")
    return r"(?<!\w)" + re.escape(stem) + r"[а-яёa-z]*"


@lru_cache(maxsize=4096)
def _token_regex(token: str) -> re.Pattern[str]:
    """Компилирует паттерн токена один раз (кеш; ``слов*`` -> стем, иначе — граница слова)."""
    if token.endswith("*"):
        return re.compile(_stem_pattern(token), re.IGNORECASE)
    return re.compile(r"(?<!\w)" + re.escape(token) + r"(?!\w)", re.IGNORECASE)


def _token_match(subject: str, token: str) -> bool:
    """Совпадение одного токена (``слов*`` или точного слова)."""
    if not token:
        return False
    return _token_regex(token).search(subject) is not None


def _proximity_match(subject: str, inner: str, distance: int) -> bool:
    """Токены ``inner``: каждый следующий — не далее ``distance`` слов ПОСЛЕ предыдущего.

    ``~N`` = не более N слов МЕЖДУ токенами (семантика как у Lucene-slack):
    «система коммерческого учета» ловится ``(систем* учет*)~1`` (1 слово между),
    но не ``~0``; «система автоматизированного коммерческого учета» — только ``~2``.
    """
    tokens = [t for t in inner.split() if t]
    if not tokens:
        return False
    words = _WORD_RE.findall(subject)
    for i, word in enumerate(words):
        if not _token_match(word, tokens[0]):
            continue
        pos = i
        ok = True
        for token in tokens[1:]:
            # ~N = не более N слов МЕЖДУ токенами (Lucene-slack): следующий токен
            # ищем в окне из N+1 слов после текущей позиции.
            window = words[pos + 1 : pos + 1 + distance + 1]
            found = next((j for j, w in enumerate(window) if _token_match(w, token)), None)
            if found is None:
                ok = False
                break
            pos = pos + 1 + found
        if ok:
            return True
    return False


def _expression_match(subject: str, expression: str) -> bool:
    """Совпадение выражения (~N -> стеб/точная фраза)."""
    prox = _PROXIMITY_RE.match(expression)
    if prox:
        return _proximity_match(subject, prox.group(1), int(prox.group(2)))
    tokens = expression.split()
    if len(tokens) == 1:
        return _token_match(subject, tokens[0])
    # Фраза: все токены (стеб-префиксы) присутствуют в тексте.
    return all(_token_match(subject, token) for token in tokens)


def keywords_match(record: dict[str, Any], keywords: list[str]) -> bool:
    """True, если хотя бы одно позитивное слово совпало (пустой список — True)."""
    if not keywords:
        return True
    subject = _subject(record)
    if not subject:
        return False
    return any(_expression_match(subject, keyword) for keyword in keywords)


def matched_keywords(record: dict[str, Any], keywords: list[str]) -> list[str]:
    """Список ключевых слов, по которым закупка прошла фильтр (R9).

    Возвращает совпавшие выражения (из ``keywords``) для записи в
    ``procurement_evaluations.matched_keywords``. Пустой список — не совпало.
    """
    if not keywords:
        return []
    subject = _subject(record)
    if not subject:
        return []
    return [keyword for keyword in keywords if _expression_match(subject, keyword)]


def exclusions_present(record: dict[str, Any], exclusion_words: list[str]) -> bool:
    """True, если любое слово-исключение совпало с описанием закупки."""
    if not exclusion_words:
        return False
    subject = _subject(record)
    if not subject:
        return False
    return any(_expression_match(subject, word) for word in exclusion_words)
