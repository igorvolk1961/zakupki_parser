"""Условия со значением-сайтом и уточнения «между значениями»."""

from __future__ import annotations

from typing import Any

import pytest

from scoring_common import object_storage
from scoring_common.conditions import (
    ConditionError,
    apply_conditions,
    evaluate_condition,
    normalize_report_fields,
    recompute_field_values,
)
from scoring_common.sources import store
from scoring_common.sources.matching import (
    SourceContext,
    TextWindows,
    clear_text_cache,
    qualifier_forms,
    source_context,
    source_contexts,
)

URL = "https://onlineecology.com/org/ooo-ekopattern"


def _row(code: str, name: str, works: str) -> str:
    return f"{code}\n{name}\n{works}\nIV класс"


# Две страницы сайта в формате onlineecology: код, название, виды работ, класс.
PAGE1 = "\n".join(
    [
        _row("1 11 010 21 49 2", "семена протравленные", "Сбор (1)  Транспортирование (1)"),
        _row("4 71 101 01 52 1", "лампы ртутные", "Транспортирование (1)  Утилизация (1)"),
        _row("7 33 100 01 72 4", "мусор офисный", "Сбор (1)"),
    ]
)
PAGE2 = "\n".join(
    [
        _row("9 19 204 02 60 4", "обтирочный материал", "Обезвреживание (1)"),
        _row("4 06 110 01 31 3", "масла моторные", "Утилизация (1)  Сбор (1)"),
        _row("7 31 110 01 72 4", "отходы из жилищ", "Транспортирование (1)"),
    ]
)


def _windows() -> TextWindows:
    return TextWindows([PAGE1, PAGE2])


def _ready(complete: bool = True) -> SourceContext:
    return SourceContext("u", "ready", complete=complete, windows=_windows())


# --- Окна ---------------------------------------------------------------------


def test_window_ends_at_next_code() -> None:
    w = _windows()
    [(page, start, end)] = w.occurrences("4 71 101 01 52 1")
    window = w.window(page, start, end, "4 71 101 01 52 1")
    assert "Утилизация" in window
    assert "7 33 100" not in window and "мусор" not in window


def test_last_code_on_page_does_not_reach_next_page() -> None:
    w = _windows()
    [(page, start, end)] = w.occurrences("7 33 100 01 72 4")
    window = w.window(page, start, end, "7 33 100 01 72 4")
    assert "Сбор" in window
    assert "Обезвреживание" not in window  # это первая строка следующей страницы


def test_last_code_capped_by_median_row_length() -> None:
    """Длинный посторонний текст после последнего кода обрезается пределом
    1.5 × медиана длины строки: слово за ним в окно не попадает. (Слово сразу
    после строки — попадёт: без структуры его от строки не отличить.)"""
    tail = "\n" + "Контакты компании, адрес, телефон. " * 6 + "Утилизация отходов."
    w = TextWindows([PAGE1 + tail])
    [(page, start, end)] = w.occurrences("7 33 100 01 72 4")
    window = w.window(page, start, end, "7 33 100 01 72 4")
    assert "Утилизация" not in window


def test_text_value_uses_symmetric_window() -> None:
    w = TextWindows(["x" * 500 + " лампы ртутные — утилизация " + "y" * 500])
    [(page, start, end)] = w.occurrences("лампы ртутные")
    window = w.window(page, start, end, "лампы ртутные", window=20)
    assert "утилизация" in window
    assert len(window) < 80


def test_qualifier_forms_by_stem() -> None:
    assert qualifier_forms("Транспортирование (1)  Утилизация (1)", "утилизации") == ["Утилизация"]
    assert qualifier_forms("Утилизировать", "утилизация") == []  # другой суффикс
    assert qualifier_forms("Утилизировать", "утилиз*") == ["Утилизировать"]


# --- Проверка по сайту --------------------------------------------------------

_ALL_IN = {"op": "all_in", "value_kind": "url", "value": URL}


def test_all_codes_found_on_complete_site() -> None:
    outcome = evaluate_condition(
        _ALL_IN, "list", ["11101021492", "4-71-101-01-52-1"], True, source=_ready()
    )
    assert outcome.match is True


def test_missing_code_on_complete_site_is_mismatch() -> None:
    outcome = evaluate_condition(
        _ALL_IN, "list", ["1 11 010 21 49 2", "3 33 333 33 33 3"], True, source=_ready()
    )
    assert outcome.match is False
    assert outcome.mismatched_values == ["3 33 333 33 33 3"]
    assert outcome.mismatch_reasons == {"3 33 333 33 33 3": "нет в источнике"}


def test_missing_code_on_incomplete_site_is_unchecked() -> None:
    outcome = evaluate_condition(
        _ALL_IN, "list", ["3 33 333 33 33 3"], True, source=_ready(complete=False)
    )
    assert outcome.match is None
    assert outcome.check_status == "source_incomplete"


def test_near_required_per_code_from_tz_windows() -> None:
    """ТЗ: утилизация для ламп, транспортирование для семян."""
    condition = {**_ALL_IN, "near": {"source": "field", "field_id": "works", "mode": "all"}}
    tz_windows = {
        "4 71 101 01 52 1": "лампы ртутные | утилизация",
        "1 11 010 21 49 2": "семена | транспортирование",
    }
    outcome = evaluate_condition(
        condition,
        "list",
        ["4 71 101 01 52 1", "1 11 010 21 49 2"],
        True,
        source=_ready(),
        qualifiers=["утилизации", "транспортирование"],
        tz_windows=tz_windows,
    )
    assert outcome.match is True
    assert outcome.requirements == {
        "4 71 101 01 52 1": ["утилизации"],
        "1 11 010 21 49 2": ["транспортирование"],
    }
    assert outcome.near_matches["утилизации"] == ["утилизация"]


def test_near_missing_is_definite_mismatch() -> None:
    """Семена на сайте есть, но утилизации рядом нет — не соответствует."""
    condition = {**_ALL_IN, "near": {"source": "words", "words": ["утилиз*"], "mode": "all"}}
    outcome = evaluate_condition(
        condition,
        "list",
        ["1 11 010 21 49 2", "4 71 101 01 52 1"],
        True,
        source=_ready(complete=False),
        qualifiers=["утилиз*"],
    )
    assert outcome.match is False
    assert outcome.mismatch_reasons == {"1 11 010 21 49 2": "нет рядом: утилиз*"}


def test_tz_without_per_code_words_requires_all() -> None:
    condition = {**_ALL_IN, "near": {"source": "field", "field_id": "works", "mode": "all"}}
    outcome = evaluate_condition(
        condition,
        "list",
        ["4 06 110 01 31 3"],
        True,
        source=_ready(),
        qualifiers=["утилизация", "сбор"],
        tz_windows={"4 06 110 01 31 3": "масла моторные"},
    )
    assert outcome.match is True
    assert outcome.requirements == {"4 06 110 01 31 3": ["утилизация", "сбор"]}


@pytest.mark.parametrize(
    ("op", "values", "complete", "expected"),
    [
        ("any_in", ["3 33 333 33 33 3", "4 71 101 01 52 1"], True, True),
        ("any_in", ["3 33 333 33 33 3"], True, False),
        ("any_in", ["3 33 333 33 33 3"], False, None),
        ("none_in", ["3 33 333 33 33 3"], True, True),
        ("none_in", ["3 33 333 33 33 3"], False, None),
        ("none_in", ["4 71 101 01 52 1"], False, False),
    ],
)
def test_other_list_operators(
    op: str, values: list[str], complete: bool, expected: bool | None
) -> None:
    condition = {"op": op, "value_kind": "url", "value": URL}
    outcome = evaluate_condition(condition, "list", values, True, source=_ready(complete))
    assert outcome.match is expected


def test_string_field_in_site() -> None:
    condition = {"op": "in", "value_kind": "url", "value": URL}
    outcome = evaluate_condition(condition, "string", "7 31 110 01 72 4", True, source=_ready())
    assert outcome.match is True


def test_source_not_ready_statuses() -> None:
    pending = SourceContext("u", "pending", collecting=True)
    failed = SourceContext("u", "failed")
    assert evaluate_condition(_ALL_IN, "list", ["x1 2"], True, source=pending).check_status == (
        "source_pending"
    )
    assert evaluate_condition(_ALL_IN, "list", ["x1 2"], True, source=failed).check_status == (
        "source_failed"
    )
    assert evaluate_condition(_ALL_IN, "list", ["x1 2"], True).check_status == "source_pending"


# --- Модель и отчёт целиком ------------------------------------------------------


def _fields(**near: Any) -> list[dict[str, Any]]:
    return [
        {"id": "works", "name": "виды работ", "type": "list"},
        {
            "id": "codes",
            "name": "коды",
            "type": "list",
            "condition": {"op": "all_in", "value_kind": "url", "value": URL, "near": near or None},
            "severity": "block",
        },
    ]


def test_normalize_url_condition_with_near_field() -> None:
    [_, codes] = normalize_report_fields(_fields(source="field", field_id="works"))
    assert codes["condition"] == {
        "op": "all_in",
        "value_kind": "url",
        "value": URL,
        "near": {
            "source": "field",
            "field_id": "works",
            "labels": [],
            "mode": "all",
            "window": 300,
            "max_window": 2000,
        },
    }


@pytest.mark.parametrize(
    ("near", "message"),
    [
        ({"source": "field", "field_id": "nope"}, "несуществующее"),
        ({"source": "field", "field_id": "codes"}, "несуществующее"),
        ({"source": "words", "words": []}, "не заданы слова"),
        ({"source": "words", "words": ["ут*"]}, "3 букв"),
        ({"source": "field", "field_id": "works", "mode": "some"}, "all или any"),
    ],
)
def test_normalize_near_rejects_invalid(near: dict[str, Any], message: str) -> None:
    with pytest.raises(ConditionError, match=message):
        normalize_report_fields(_fields(**near))


def test_url_condition_not_for_numbers() -> None:
    with pytest.raises(ConditionError):
        normalize_report_fields(
            [
                {
                    "id": "n",
                    "name": "объём",
                    "type": "number",
                    "condition": {"op": "in", "value_kind": "url", "value": URL},
                }
            ]
        )


def test_apply_conditions_takes_qualifiers_from_other_field() -> None:
    defs = normalize_report_fields(_fields(source="field", field_id="works"))
    values = [
        {"field_id": "works", "field_type": "list", "found": True, "value": ["утилизация"]},
        {
            "field_id": "codes",
            "field_type": "list",
            "found": True,
            "value": ["4 71 101 01 52 1", "1 11 010 21 49 2"],
        },
    ]
    from scoring_common.sources.urls import normalize_source_url

    sources = {normalize_source_url(URL): _ready()}
    checked = {v["field_id"]: v for v in apply_conditions(values, defs, sources)}
    codes = checked["codes"]
    assert codes["match"] is False
    assert codes["mismatch_reasons"] == {"1 11 010 21 49 2": "нет рядом: утилизация"}
    assert codes["source_status"]["state"] == "ready"
    assert "source" not in codes["condition"]


def test_recompute_uses_source_without_llm() -> None:
    from scoring_common.conditions import extraction_key
    from scoring_common.sources.urls import normalize_source_url

    defs = normalize_report_fields(_fields())
    stored = [
        {
            "field_id": d["id"],
            "field_type": "list",
            "found": True,
            "value": ["4 71 101 01 52 1"] if d["id"] == "codes" else ["сбор"],
            "extraction_key": extraction_key(d),
        }
        for d in defs
    ]
    pending, complete = recompute_field_values(stored, defs, {})
    assert complete is True
    assert {v["field_id"]: v for v in pending}["codes"]["check_status"] == "source_pending"
    ready, _ = recompute_field_values(stored, defs, {normalize_source_url(URL): _ready()})
    assert {v["field_id"]: v for v in ready}["codes"]["match"] is True


# --- Текст сайта из хранилища ---------------------------------------------------


def test_source_context_reads_store_and_caches() -> None:
    clear_text_cache()
    memory = object_storage.use_in_memory()
    url_norm = "https://onlineecology.com/org/ooo-ekopattern"
    store.put_text(store.text_key(url_norm), store.join_pages([("p1", PAGE1), ("p2", PAGE2)]))
    meta = {
        "url_norm": url_norm,
        "status": "complete",
        "text_complete": True,
        "fetched_at": "2026-09-25T10:00:00+00:00",
    }
    ctx = source_context(meta)
    assert ctx.state == "ready" and ctx.complete
    assert ctx.windows is not None and len(ctx.windows.pages) == 2
    memory.store.clear()
    assert source_context(meta).windows is ctx.windows  # из кэша процесса


def test_source_context_not_collected_yet() -> None:
    ctx = source_context({"url_norm": "https://x.ru", "status": "running", "active": True})
    assert ctx.state == "pending" and ctx.collecting
    failed = source_context({"url_norm": "https://x.ru", "status": "failed"})
    assert failed.state == "failed"


def test_source_contexts_collects_from_field_defs() -> None:
    defs = [
        {
            "condition": {
                "value_kind": "url",
                "source": {"url_norm": "https://a", "status": "failed"},
            }
        },
        {"condition": {"value_kind": "list"}},
    ]
    assert list(source_contexts(defs)) == ["https://a"]


# --- Форма уточнения на сайте: точная (слова) и метки сайта -----------------------

# Как на onlineecology: слово «утилизации» в НАЗВАНИИ отхода, метки — отдельно.
_NAME_TRAP = "\n".join(
    [
        _row("4 06 329 01 31 3", "смесь масел, пригодная для утилизации", "Сбор (1)"),
        _row("4 71 101 01 52 1", "лампы ртутные", "Транспортирование (1)  Утилизация (1)"),
        _row("7 33 100 01 72 4", "мусор", "Сбор (1)"),
    ]
)


def _trap_source() -> SourceContext:
    return SourceContext("u", "ready", complete=True, windows=TextWindows([_NAME_TRAP]))


def test_word_without_asterisk_is_exact_form() -> None:
    condition = {**_ALL_IN, "near": {"source": "words", "words": ["утилизация"]}}
    outcome = evaluate_condition(
        condition,
        "list",
        ["4 06 329 01 31 3", "4 71 101 01 52 1"],
        True,
        source=_trap_source(),
        qualifiers=["утилизация"],
    )
    assert outcome.match is False
    assert outcome.mismatch_reasons == {"4 06 329 01 31 3": "нет рядом: утилизация"}


def test_stem_search_falls_for_word_in_name() -> None:
    """Без меток сайта (по основе) слово в названии засчитывается — поэтому метки."""
    condition = {**_ALL_IN, "near": {"source": "field", "field_id": "works"}}
    outcome = evaluate_condition(
        condition,
        "list",
        ["4 06 329 01 31 3"],
        True,
        source=_trap_source(),
        qualifiers=["утилизации"],
    )
    assert outcome.match is True


def test_tz_word_mapped_to_site_label_exactly() -> None:
    labels = ["Сбор", "Транспортирование", "Утилизация", "Обезвреживание"]
    condition = {
        **_ALL_IN,
        "near": {"source": "field", "field_id": "works", "labels": labels},
    }
    outcome = evaluate_condition(
        condition,
        "list",
        ["4 06 329 01 31 3", "4 71 101 01 52 1"],
        True,
        source=_trap_source(),
        qualifiers=["утилизации", "транспортированию"],
        tz_windows={
            "4 06 329 01 31 3": "смесь масел | утилизации",
            "4 71 101 01 52 1": "лампы | транспортированию, утилизации",
        },
    )
    assert outcome.match is False
    assert outcome.mismatch_reasons == {"4 06 329 01 31 3": "нет рядом: утилизации"}
    assert outcome.near_labels == {
        "утилизации": "Утилизация",
        "транспортированию": "Транспортирование",
    }


def test_tz_word_without_site_label() -> None:
    condition = {
        **_ALL_IN,
        "near": {"source": "field", "field_id": "works", "labels": ["Сбор", "Утилизация"]},
    }
    outcome = evaluate_condition(
        condition,
        "list",
        ["7 33 100 01 72 4"],
        True,
        source=_trap_source(),
        qualifiers=["размещения"],
    )
    assert outcome.match is False
    assert outcome.mismatch_reasons == {"7 33 100 01 72 4": "нет в списке меток сайта: размещения"}
    assert outcome.near_labels == {"размещения": None}


def test_normalize_near_labels() -> None:
    [_, codes] = normalize_report_fields(
        _fields(source="field", field_id="works", labels="Сбор, Утилизация\nСбор")
    )
    assert codes["condition"]["near"]["labels"] == ["Сбор", "Утилизация"]
