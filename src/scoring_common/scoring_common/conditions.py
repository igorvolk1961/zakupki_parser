"""Условия отчётных полей профиля: модель, поиск значений в тексте, проверка.

Отчётное поле профиля (FR-12.1) извлекается LLM из документов закупки; условие
на поле (оператор + значение) проверяется КОДОМ, без LLM — поэтому его можно
пересчитать по уже извлечённым значениям, когда пользователь меняет условие
(``recompute_field_values``). Исключение — оператор ``llm`` («соответствует по
смыслу»): его оценивает LLM в момент извлечения.

Поиск значения в тексте не требует от пользователя регулярных выражений и не
знает структуры конкретных кодов — шаблон строится из самого значения:

- **код** (цифр не меньше половины букв+цифр: ``1 11 010 21 49 2``,
  ``62.01.11``, ``ГОСТ 12.1.004``) — буквы/цифры значения в том же порядке,
  между ними необязательный одиночный разделитель ``[ .-/]``;
- **текст** — слова значения по основам (Snowball, отбрасывается окончание)
  в том же порядке, между словами только пробелы/знаки препинания. Слово со
  звёздочкой на конце (``утилиз*``) — явный префикс, без основы.

Модуль — чистый код без I/O: используется analysis_service (извлечение) и API
(валидация профиля, пересчёт условий без LLM).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any, Literal

import snowballstemmer  # type: ignore[import-untyped]

FIELD_TYPES = ("string", "number", "date", "boolean", "list")
VALUE_MODES = ("auto", "code", "text")

# Оператор -> (вид значения условия, допустимые типы поля).
OPERATORS: dict[str, tuple[str, frozenset[str]]] = {
    "eq": ("scalar", frozenset({"string", "number", "date", "boolean"})),
    "ne": ("scalar", frozenset({"string", "number", "date", "boolean"})),
    "gt": ("scalar", frozenset({"number", "date"})),
    "gte": ("scalar", frozenset({"number", "date"})),
    "lt": ("scalar", frozenset({"number", "date"})),
    "lte": ("scalar", frozenset({"number", "date"})),
    "contains": ("scalar", frozenset({"string", "list"})),
    "in": ("list", frozenset({"string", "number", "list"})),
    "not_in": ("list", frozenset({"string", "number", "list"})),
    "all_in": ("list", frozenset({"list"})),
    "any_in": ("list", frozenset({"list"})),
    "none_in": ("list", frozenset({"list"})),
    "llm": ("scalar", frozenset(FIELD_TYPES)),
}

# Минимум букв/цифр в значении-коде: короче — слишком много ложных совпадений.
MIN_CODE_CHARS = 3

CheckStatus = Literal[
    "ok",
    "no_condition",
    "not_found_in_tz",
    "llm_failed",
    "needs_reanalysis",
    "invalid_value",
]


class ConditionError(ValueError):
    """Некорректное описание отчётного поля/условия (ошибка ввода, 422)."""


# ---------------------------------------------------------------------- #
# Нормализация и вид значения
# ---------------------------------------------------------------------- #

_ALNUM = "0-9a-zа-я"
_ALNUM_CLASS = f"[{_ALNUM}]"
_NOT_ALNUM = f"(?<![{_ALNUM}])"
_NOT_ALNUM_AHEAD = f"(?![{_ALNUM}])"
_WORD_RE = re.compile(f"{_ALNUM_CLASS}+\\*?")
_CODE_SEP = "[ .\\-/]?"
# Между словами текстового значения — любые не-буквы/цифры, кроме перевода строки.
_TEXT_SEP = f"[^{_ALNUM}\\n]+"


# Неразрывные/узкие пробелы, табуляция -> пробел; тире/дефисы -> «-» (1:1 по длине).
_CHAR_MAP = str.maketrans(
    {
        "ё": "е",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        "\t": " ",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
    }
)


def normalize_text(text: str) -> str:
    """Нижний регистр, ``ё→е``, единые пробел/дефис — без изменения длины
    (позиции совпадений в нормализованном тексте совпадают с исходным)."""
    lowered = "".join(low if len(low := ch.lower()) == 1 else ch for ch in text)
    return lowered.translate(_CHAR_MAP)


def classify_value(value: str, mode: str = "auto") -> Literal["code", "text"]:
    """Вид значения: явный (``mode``) или по доле цифр среди букв/цифр."""
    if mode in ("code", "text"):
        return mode  # type: ignore[return-value]
    alnum = [ch for ch in value if ch.isalnum()]
    digits = sum(ch.isdigit() for ch in alnum)
    return "code" if digits and digits * 2 >= len(alnum) else "text"


def canonical_code(value: str) -> str:
    """Каноническая форма кода — только буквы/цифры в нижнем регистре."""
    return "".join(ch for ch in normalize_text(value) if ch.isalnum())


@lru_cache(maxsize=2)
def _stemmer(lang: str) -> Any:
    return snowballstemmer.stemmer(lang)


@lru_cache(maxsize=65536)
def stem_word(word: str) -> str:
    """Основа слова (отброшено окончание); слово с цифрами — как есть."""
    word = normalize_text(word)
    if any(ch.isdigit() for ch in word):
        return word
    lang = "russian" if re.search("[а-я]", word) else "english"
    stem: str = _stemmer(lang).stemWord(word)
    # Слишком короткая основа («сбор» -> ...) почти ничего не отличает.
    return stem if len(stem) >= 3 else word


def stem_pattern(word: str) -> str:
    """Подсказка для UI: слово -> шаблон со звёздочкой (``утилизация`` -> ``утилизац*``)."""
    base = stem_word(word)
    return f"{base}*" if len(base) >= 3 else base


def _text_words(value: str) -> list[str]:
    return _WORD_RE.findall(normalize_text(value))


def canonical_text(value: str) -> str:
    """Каноническая форма текста — основы слов через пробел."""
    return " ".join(w[:-1] if w.endswith("*") else stem_word(w) for w in _text_words(value))


def canonical_value(value: str, mode: str = "auto") -> str:
    kind = classify_value(value, mode)
    return canonical_code(value) if kind == "code" else canonical_text(value)


# ---------------------------------------------------------------------- #
# Шаблоны поиска
# ---------------------------------------------------------------------- #


@lru_cache(maxsize=16384)
def _code_regex(value: str) -> re.Pattern[str] | None:
    chars = canonical_code(value)
    if len(chars) < MIN_CODE_CHARS:
        return None
    core = _CODE_SEP.join(re.escape(ch) for ch in chars)
    return re.compile(f"{_NOT_ALNUM}{core}{_NOT_ALNUM_AHEAD}")


def _text_core(value: str) -> str | None:
    words = _text_words(value)
    if not words:
        return None
    parts: list[str] = []
    explicit = any(w.endswith("*") for w in words)
    for word in words:
        if word.endswith("*"):
            parts.append(re.escape(word[:-1]) + f"{_ALNUM_CLASS}*")
        elif explicit or any(ch.isdigit() for ch in word):
            # Явный режим (есть «*»): слова без звёздочки — целиком.
            parts.append(re.escape(word) + _NOT_ALNUM_AHEAD)
        else:
            base = stem_word(word)
            # Короткие слова («и», «в») — целиком, остальные — основа + любое окончание.
            tail = f"{_ALNUM_CLASS}*" if len(base) >= 3 else _NOT_ALNUM_AHEAD
            parts.append(re.escape(base) + tail)
    return _TEXT_SEP.join(parts)


@lru_cache(maxsize=16384)
def _text_regex(value: str) -> re.Pattern[str] | None:
    core = _text_core(value)
    return re.compile(f"{_NOT_ALNUM}{core}") if core else None


@lru_cache(maxsize=16384)
def _text_full_regex(value: str) -> re.Pattern[str] | None:
    core = _text_core(value)
    return re.compile(f"[^{_ALNUM}]*{core}[^{_ALNUM}]*") if core else None


def value_regex(value: str, mode: str = "auto") -> re.Pattern[str] | None:
    """Шаблон поиска значения в НОРМАЛИЗОВАННОМ тексте (``normalize_text``)."""
    if classify_value(value, mode) == "code":
        return _code_regex(value)
    return _text_regex(value)


def find_value(normalized_text: str, value: str, mode: str = "auto") -> list[tuple[int, int]]:
    """Все вхождения значения в нормализованном тексте: ``[(start, end)]``."""
    regex = value_regex(value, mode)
    if regex is None:
        return []
    return [m.span() for m in regex.finditer(normalized_text)]


def values_equal(a: str, b: str, mode: str = "auto") -> bool:
    """Равенство двух строковых значений с учётом вида (код/текст)."""
    kind_a, kind_b = classify_value(a, mode), classify_value(b, mode)
    if kind_a == "code" and kind_b == "code":
        return canonical_code(a) == canonical_code(b) and bool(canonical_code(a))
    na, nb = normalize_text(a), normalize_text(b)
    for pattern_src, target in ((b, na), (a, nb)):
        regex = _text_full_regex(pattern_src)
        if regex is not None and regex.fullmatch(target):
            return True
    return False


def value_shape(value: str) -> re.Pattern[str] | None:
    """Форма значения-кода с разделителями: ``1 11 010`` -> ``\\d \\d{2} \\d{3}``.

    ``None`` — значение без разделителей между группами (``11101021492``:
    форма ``\\d{11}`` ловила бы телефоны и прочие номера) или не код.
    """
    if classify_value(value) != "code":
        return None
    norm = normalize_text(value.strip())
    parts: list[str] = []
    groups = 0
    has_sep = False
    i = 0
    while i < len(norm):
        ch = norm[i]
        if ch.isdigit() or ch.isalpha():
            is_digit = ch.isdigit()
            j = i
            while j < len(norm) and (norm[j].isdigit() if is_digit else norm[j].isalpha()):
                j += 1
            n = j - i
            cls = "\\d" if is_digit else "[a-zа-я]"
            parts.append(cls + (f"{{{n}}}" if n > 1 else ""))
            groups += 1
            i = j
        else:
            j = i
            while j < len(norm) and not norm[j].isalnum():
                j += 1
            sep = norm[i:j]
            if not re.fullmatch("[ .\\-/]+", sep):
                return None
            parts.append(re.escape(sep))
            has_sep = has_sep or groups > 0
            i = j
    if groups < 2 or not has_sep:
        return None
    return re.compile(f"{_NOT_ALNUM}{''.join(parts)}{_NOT_ALNUM_AHEAD}")


# ---------------------------------------------------------------------- #
# Модель отчётного поля и условия
# ---------------------------------------------------------------------- #


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = re.split(r"[\n;,]", value)
    elif isinstance(value, Iterable):
        items = [str(v) for v in value if v is not None]
    else:
        return []
    return [s for s in (i.strip() for i in items) if s]


def normalize_condition(raw: Any, field_type: str, mode: str = "auto") -> dict[str, Any] | None:
    """Проверенное условие поля или ``None`` (условие не задано).

    Raises:
        ConditionError: неизвестный оператор, оператор не подходит к типу поля,
            пустое/некорректное значение.
    """
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, Mapping):
        raise ConditionError("Условие должно быть объектом {op, value}")
    op = _str(raw.get("op"))
    if op not in OPERATORS:
        raise ConditionError(f"Неизвестный оператор условия: {op or '—'}")
    kind, types = OPERATORS[op]
    if field_type not in types:
        raise ConditionError(f"Оператор «{op}» не применим к полю типа «{field_type}»")
    value_kind = _str(raw.get("value_kind")) or kind
    if value_kind == "url":
        raise ConditionError("Условие со значением-сайтом пока не поддерживается")
    if value_kind != kind:
        raise ConditionError(f"Оператору «{op}» нужно значение вида «{kind}»")
    if kind == "list":
        items = list(dict.fromkeys(_as_list(raw.get("value"))))
        if not items:
            raise ConditionError("Список значений условия пуст")
        if field_type == "number":
            for item in items:
                if _parse_number(item) is None:
                    raise ConditionError(f"Не число в списке условия: {item}")
        else:
            for item in items:
                _check_searchable(item, mode)
        return {"op": op, "value_kind": "list", "value": items}
    value = _str(raw.get("value"))
    if not value:
        raise ConditionError("Значение условия не задано")
    if op != "llm":
        parsed_ok = {
            "number": _parse_number,
            "date": _parse_date,
            "boolean": _parse_bool,
        }.get(field_type)
        if parsed_ok is not None and parsed_ok(value) is None:
            raise ConditionError(f"Значение «{value}» не подходит к типу поля «{field_type}»")
        if field_type in ("string", "list"):
            _check_searchable(value, mode)
    return {"op": op, "value_kind": "scalar", "value": value}


def _check_searchable(value: str, mode: str) -> None:
    if classify_value(value, mode) == "code" and len(canonical_code(value)) < MIN_CODE_CHARS:
        raise ConditionError(
            f"Значение «{value}» слишком короткое (нужно не меньше {MIN_CODE_CHARS} букв/цифр)"
        )
    for word in _text_words(value):
        if word.endswith("*") and len(word) - 1 < 3:
            raise ConditionError(f"Перед «*» нужно не меньше 3 букв: {word}")


def normalize_report_fields(fields: Any) -> list[dict[str, Any]]:
    """Отчётные поля профиля в каноническом виде (запись профиля).

    ``blocking`` без условия сбрасывается — нечего нарушать.

    Raises:
        ConditionError: с именем поля в сообщении.
    """
    if fields is None:
        return []
    if not isinstance(fields, list):
        raise ConditionError("report_fields должен быть списком")
    out: list[dict[str, Any]] = []
    for raw in fields:
        if not isinstance(raw, Mapping):
            raise ConditionError("Отчётное поле должно быть объектом")
        name = _str(raw.get("name"))
        field_id = _str(raw.get("id"))
        if not name or not field_id:
            raise ConditionError("У отчётного поля должны быть id и название")
        field_type = _str(raw.get("type")) or "string"
        if field_type not in FIELD_TYPES:
            raise ConditionError(f"Поле «{name}»: неизвестный тип {field_type}")
        mode = _str(raw.get("value_mode")) or "auto"
        if mode not in VALUE_MODES:
            raise ConditionError(f"Поле «{name}»: неизвестный вид значений {mode}")
        try:
            condition = normalize_condition(raw.get("condition"), field_type, mode)
        except ConditionError as exc:
            raise ConditionError(f"Поле «{name}»: {exc}") from exc
        entry: dict[str, Any] = {
            "id": field_id,
            "name": name,
            "hint": _str(raw.get("hint")) or None,
            "type": field_type,
            "unit": (_str(raw.get("unit")) or None) if field_type == "number" else None,
            "value_mode": mode,
            "condition": condition,
            "blocking": bool(raw.get("blocking")) and condition is not None,
        }
        if field_type == "list":
            extend = raw.get("extend_list")
            entry["extend_list"] = True if extend is None else bool(extend)
        out.append(entry)
    return out


def extraction_key(field_def: Mapping[str, Any]) -> str:
    """Отпечаток того, ЧТО извлекается (без условия): смена — нужен повторный LLM."""
    payload = {
        k: field_def.get(k) for k in ("name", "hint", "type", "unit", "value_mode", "extend_list")
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------- #
# Проверка условия
# ---------------------------------------------------------------------- #


def _parse_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(float(value)) else None
    text = re.sub(r"[\s ]", "", str(value)).replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _parse_date(value: Any) -> date | None:
    text = _str(value)
    for pattern, order in (
        (r"(\d{4})-(\d{2})-(\d{2})", "ymd"),
        (r"(\d{2})\.(\d{2})\.(\d{4})", "dmy"),
    ):
        m = re.fullmatch(pattern, text)
        if m:
            a, b, c = (int(g) for g in m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


_TRUE = {"да", "true", "1", "yes", "истина"}
_FALSE = {"нет", "false", "0", "no", "ложь"}


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = normalize_text(_str(value))
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


@dataclass
class ConditionOutcome:
    """Итог проверки условия по значению поля."""

    match: bool | None
    check_status: CheckStatus
    mismatched_values: list[str] = field(default_factory=list)


def _compare(op: str, left: Any, right: Any) -> bool:
    return bool(
        {
            "eq": left == right,
            "ne": left != right,
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[op]
    )


def _in_list(item: str, options: Sequence[str], field_type: str, mode: str) -> bool:
    if field_type == "number":
        number = _parse_number(item)
        return number is not None and any(_parse_number(o) == number for o in options)
    return any(values_equal(item, option, mode) for option in options)


def evaluate_condition(
    condition: Mapping[str, Any] | None,
    field_type: str,
    value: Any,
    found: bool,
    *,
    value_mode: str = "auto",
    llm_match: bool | None = None,
) -> ConditionOutcome:
    """Проверка условия по извлечённому значению поля (без LLM, кроме ``op=llm``).

    ``llm_match`` — оценка LLM, полученная при извлечении (только ``op=llm``).
    Для списков ``mismatched_values`` — значения, нарушившие условие: для
    ``all_in``/``in`` — не найденные в списке условия, для ``none_in``/
    ``not_in`` — найденные в нём.
    """
    if not condition:
        return ConditionOutcome(None, "no_condition")
    op = str(condition.get("op"))
    target = condition.get("value")
    if op == "llm":
        if not found:
            return ConditionOutcome(None, "not_found_in_tz")
        if llm_match is None:
            return ConditionOutcome(None, "llm_failed")
        return ConditionOutcome(llm_match, "ok")
    items = [str(v) for v in value] if isinstance(value, list) else None
    if not found or value is None or (items is not None and not items):
        return ConditionOutcome(None, "not_found_in_tz")

    if field_type == "list" and items is not None:
        options = [str(v) for v in target] if isinstance(target, list) else []
        if op == "contains":
            return ConditionOutcome(
                any(values_equal(i, str(target), value_mode) for i in items), "ok"
            )
        inside = [i for i in items if _in_list(i, options, "list", value_mode)]
        outside = [i for i in items if i not in inside]
        if op in ("in", "all_in"):
            return ConditionOutcome(not outside, "ok", outside)
        if op == "any_in":
            return ConditionOutcome(bool(inside), "ok")
        if op in ("not_in", "none_in"):
            return ConditionOutcome(not inside, "ok", inside)
        return ConditionOutcome(None, "invalid_value")

    if op in ("in", "not_in"):
        options = [str(v) for v in target] if isinstance(target, list) else []
        hit = _in_list(str(value), options, field_type, value_mode)
        ok = hit if op == "in" else not hit
        return ConditionOutcome(ok, "ok", [] if ok else [str(value)])

    if op == "contains":
        found_in = bool(find_value(normalize_text(str(value)), str(target), value_mode))
        return ConditionOutcome(found_in, "ok")

    parser = {"number": _parse_number, "date": _parse_date, "boolean": _parse_bool}.get(field_type)
    if parser is not None:
        left, right = parser(value), parser(target)
        if left is None or right is None:
            return ConditionOutcome(None, "invalid_value")
        if field_type == "boolean" and op not in ("eq", "ne"):
            return ConditionOutcome(None, "invalid_value")
        return ConditionOutcome(_compare(op, left, right), "ok")
    if op in ("eq", "ne"):
        equal = values_equal(str(value), str(target), value_mode)
        return ConditionOutcome(equal if op == "eq" else not equal, "ok")
    return ConditionOutcome(None, "invalid_value")


def apply_condition(field_value: dict[str, Any], field_def: Mapping[str, Any]) -> dict[str, Any]:
    """Проставляет в значение поля результат проверки условия из ``field_def``.

    Меняются только ``condition``/``blocking``/``match``/``check_status``/
    ``mismatched_values``; извлечённое значение не трогается.
    """
    condition = field_def.get("condition")
    llm_match = field_value.get("llm_match")
    outcome = evaluate_condition(
        condition,
        str(field_value.get("field_type") or field_def.get("type") or "string"),
        field_value.get("value"),
        bool(field_value.get("found")),
        value_mode=str(field_def.get("value_mode") or "auto"),
        llm_match=llm_match if isinstance(llm_match, bool) else None,
    )
    updated = dict(field_value)
    updated["condition"] = dict(condition) if condition else None
    updated["blocking"] = bool(field_def.get("blocking")) and condition is not None
    updated["match"] = outcome.match
    updated["check_status"] = outcome.check_status
    updated["mismatched_values"] = outcome.mismatched_values[:50]
    return updated


def recompute_field_values(
    stored: Sequence[Mapping[str, Any]], field_defs: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    """Пересчёт условий по сохранённым значениям полей (без LLM).

    Возвращает ``(values, complete)``: ``complete=False`` — хотя бы одно поле
    нельзя пересчитать без повторного извлечения (новое поле, изменилось
    название/подсказка/тип, изменилось LLM-условие) — такие поля остаются
    как были (или помечаются ``needs_reanalysis``), отчёт устарел.
    Значения удалённых из профиля полей отбрасываются.
    """
    by_id = {str(v.get("field_id")): v for v in stored if isinstance(v, Mapping)}
    out: list[dict[str, Any]] = []
    complete = True
    for field_def in field_defs:
        current = by_id.get(str(field_def.get("id")))
        if current is None:
            complete = False
            continue
        if current.get("extraction_key") != extraction_key(field_def):
            complete = False
            out.append(dict(current))
            continue
        condition = field_def.get("condition") or None
        if condition and condition.get("op") == "llm":
            previous = current.get("condition") or {}
            if previous.get("op") != "llm" or previous.get("value") != condition.get("value"):
                complete = False
                updated = apply_condition({**current, "llm_match": None}, field_def)
                updated["check_status"] = "needs_reanalysis"
                out.append(updated)
                continue
        out.append(apply_condition(dict(current), field_def))
    return out, complete


__all__ = [
    "FIELD_TYPES",
    "OPERATORS",
    "ConditionError",
    "ConditionOutcome",
    "apply_condition",
    "canonical_value",
    "classify_value",
    "evaluate_condition",
    "extraction_key",
    "find_value",
    "normalize_condition",
    "normalize_report_fields",
    "normalize_text",
    "recompute_field_values",
    "stem_pattern",
    "stem_word",
    "value_regex",
    "value_shape",
    "values_equal",
]
