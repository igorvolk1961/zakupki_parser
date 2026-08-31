"""Сборка серверной предфильтрации по ключевым словам профиля.

Клиентская фильтрация (R9, ``parser.filtering``) остаётся финальной: серверная
строка лишь сужает выдачу до потенциально релевантных закупок. Используется для
площадок без серверного ОКПД2 (например b2b-center, параметр ``f_keyword``).

Собирается по формату, проверенному на b2b-center (см. правку предфильтра):
- одиночные слова (``слов*``/``слово``) идут как есть;
- многословные выражения (``фраза1 фраза2``) оборачиваются в круглые скобки
  (площадка трактует их как фразу, а не «все слова где угодно»);
- выражения-проксимити ``(фраза)~N`` приводятся к фразе: скобки сохраняются,
  суффикс ``~N`` отбрасывается (у площадок нет семантики ``~N``);
- все части соединяются союзом «или».
"""

from __future__ import annotations

import re
import urllib.parse

# Проксимити-выражение вида ``(w1 w2)~N`` (см. parser.filtering._PROXIMITY_RE).
_PROXIMITY_RE = re.compile(r"^\((.+)\)~(\d+)$")


# Безопасный предел длины URL-кодированной строки предфильтрации. При превышении
# строка не передаётся (запрос начал бы упираться в лимиты URI серверов/браузеров
# ~8k) — предфильтрация пропускается, финальная фильтрация по словам остаётся
# клиентской (R9). Слишком длинную предфильтрацию нужно дробить/ограничивать набор
# слов (см. длину строки для полного профиля bbk-it).
MAX_KEYWORD_QUERY_ENC_LEN = 6000


def is_proximity(expr: str) -> bool:
    """True, если выражение — проксимити ``(w1 w2)~N``."""
    return _PROXIMITY_RE.match(expr) is not None


def keyword_search_string(
    keywords: list[str],
    join: str = " или ",
) -> str:
    """Собирает серверную строку поиска по позитивным словам профиля.

    ``keywords`` — исходные выражения (таблица ``keywords``, type=keyword).
    Проксимити ``(w1 w2)~N`` приводятся к ``(w1 w2)`` (скобки остаются, ``~N``
    убирается); многословные выражения оборачиваются в скобки; одиночные — как есть.
    Части соединяются ``join`` (союз «или»).

    Возвращает пустую строку, если слов нет.
    """
    parts: list[str] = []
    for keyword in keywords:
        expr = keyword.strip().strip("\"'")
        if not expr:
            continue
        prox = _PROXIMITY_RE.match(expr)
        if prox:
            expr = f"({prox.group(1).strip()})"
        elif " " in expr:
            expr = f"({expr})"
        parts.append(expr)
    return join.join(parts)


def keyword_batches(
    keywords: list[str],
    max_enc_len: int = MAX_KEYWORD_QUERY_ENC_LEN,
    join: str = " или ",
) -> list[list[str]]:
    """Дробит ключевые слова на батчи, каждый вписывается в лимит длины запроса.

    Строка предфильтрации `keyword_search_string(батч)` после URL-кодирования не
    должна превышать ``max_enc_len`` (иначе запрос сломается). Ключевые слова
    объединяются жадным алгоритмом с сохранением исходного порядка: слово добавляется
    в текущий батч, пока тот не превысит лимит, иначе батч закрывается и начинается
    новый. Если одно слово не влезает даже в пустой батч — оно попадает в собственный
    батч как есть (обработчик предфильтрации откажет по длине, но слово не потеряется).

    Возвращает список батчей; при отсутствии слов — `[[]]` (один пустой батч =
    обход без серверной предфильтрации).
    """

    def _encoded(batch: list[str]) -> int:
        return len(urllib.parse.quote(keyword_search_string(batch, join=join), safe=""))

    if not keywords:
        return [[]]
    batches: list[list[str]] = []
    current: list[str] = []
    for keyword in keywords:
        candidate = current + [keyword]
        if _encoded(candidate) <= max_enc_len or not current:
            current = candidate
        else:
            batches.append(current)
            current = [keyword]
    if current:
        batches.append(current)
    return batches
