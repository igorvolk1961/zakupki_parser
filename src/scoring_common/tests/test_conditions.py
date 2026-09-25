"""Unit-тесты условий отчётных полей (scoring_common.conditions)."""

from __future__ import annotations

from typing import Any

import pytest

from scoring_common.conditions import (
    ConditionError,
    apply_condition,
    canonical_value,
    classify_value,
    evaluate_condition,
    extraction_key,
    find_value,
    normalize_report_fields,
    normalize_text,
    recompute_field_values,
    stem_pattern,
    value_shape,
    values_equal,
)

# --- Вид значения ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("1 11 010 21 49 2", "code"),
        ("11101021492", "code"),
        ("62.01.11", "code"),
        ("ГОСТ 12.1.004", "code"),
        ("A-123-BX", "code"),
        ("отходы бумаги", "text"),
        ("класс 4", "text"),
    ],
)
def test_classify_value(value: str, kind: str) -> None:
    assert classify_value(value) == kind


def test_classify_value_explicit_mode_wins() -> None:
    assert classify_value("отходы", "code") == "code"
    assert classify_value("123", "text") == "text"


def test_normalize_text_keeps_length() -> None:
    text = "Ёлка 1 11 — Код"
    assert len(normalize_text(text)) == len(text)
    assert normalize_text(text) == "елка 1 11 - код"


# --- Поиск кодов -------------------------------------------------------------

_TEXT = normalize_text(
    "Коды: 1 11 010 21 49 2; 11101021492; 1-11-010-21-49-2; 1 11 010 21 49 2; "
    "не код 111010214921 и не 211101021492."
)


def test_code_found_in_any_notation() -> None:
    assert len(find_value(_TEXT, "1 11 010 21 49 2")) == 4


def test_code_not_found_inside_longer_number() -> None:
    spans = find_value(_TEXT, "11101021492")
    found = [_TEXT[s:e] for s, e in spans]
    assert "111010214921" not in found
    assert len(spans) == 4


def test_code_shorter_than_minimum_is_not_searched() -> None:
    assert find_value("12 и 12", "12") == []


def test_code_does_not_span_lines() -> None:
    assert find_value(normalize_text("1 11 010\n21 49 2"), "1 11 010 21 49 2") == []


# --- Поиск текста по основам ---------------------------------------------------


def test_text_found_in_other_case_forms() -> None:
    text = normalize_text("Вывоз отходов бумаги и картона. ОТХОДЫ БУМАГИ.")
    assert len(find_value(text, "отходы бумаги")) == 2


def test_text_different_suffix_not_matched() -> None:
    """Сводятся только окончания: «бумажный» и «бумаги» — разные основы."""
    assert find_value(normalize_text("бумажные отходы"), "отходы бумаги") == []


def test_text_word_order_matters() -> None:
    assert find_value(normalize_text("бумаги отходы"), "отходы бумаги") == []


def test_text_punctuation_between_words_allowed_newline_not() -> None:
    assert find_value(normalize_text("отходы | бумаги"), "отходы бумаги")
    assert not find_value(normalize_text("отходы\nбумаги"), "отходы бумаги")


def test_text_explicit_asterisk_prefix() -> None:
    text = normalize_text("Утилизировать и утилизация")
    assert len(find_value(text, "утилиз*")) == 2
    # Без звёздочки в явном режиме слово — целиком.
    assert len(find_value(text, "утилизация сбор*")) == 0


def test_text_word_boundary_at_start() -> None:
    assert find_value(normalize_text("переработка"), "работа") == []


def test_stem_pattern_hint() -> None:
    assert stem_pattern("утилизации") == "утилизац*"
    assert stem_pattern("Утилизация") == "утилизац*"


# --- Равенство и каноническая форма ---------------------------------------------


def test_values_equal_codes_by_digits() -> None:
    assert values_equal("1-11-010-21-49-2", "11101021492")
    assert not values_equal("1 11 010 21 49 2", "1 11 010 21 49 3")


def test_values_equal_text_by_stems() -> None:
    assert values_equal("отходов бумаги", "Отходы бумаги.")
    assert not values_equal("отходы бумаги", "отходы бумаги и картона")


def test_canonical_value() -> None:
    assert canonical_value("1-11-010") == "111010"
    assert canonical_value("Отходов бумаги") == canonical_value("отходы бумаги")


# --- Форма кода --------------------------------------------------------------


def test_value_shape_from_code_with_separators() -> None:
    shape = value_shape("1 11 010 21 49 2")
    assert shape is not None
    text = normalize_text("4 71 101 01 52 1, тел. 8 912 345 67 89, 11101021492")
    assert [m.group() for m in shape.finditer(text)] == ["4 71 101 01 52 1"]


def test_value_shape_none_without_separators_or_for_text() -> None:
    assert value_shape("11101021492") is None
    assert value_shape("отходы бумаги") is None


def test_value_shape_letters_and_dots() -> None:
    shape = value_shape("62.01.11")
    assert shape is not None
    assert [m.group() for m in shape.finditer("62.01.12 и 01.02.2024")] == ["62.01.12"]


# --- Проверка условия ----------------------------------------------------------


def _cond(op: str, value: Any) -> dict[str, Any]:
    return {"op": op, "value": value}


@pytest.mark.parametrize(
    ("op", "target", "value", "expected"),
    [
        ("gte", "500 000", 600000.0, True),
        ("gte", "500000", 400000.0, False),
        ("lt", "10,5", 10.0, True),
        ("eq", "100", 100.0, True),
        ("ne", "100", 100.0, False),
    ],
)
def test_number_comparisons(op: str, target: str, value: float, expected: bool) -> None:
    outcome = evaluate_condition(_cond(op, target), "number", value, True)
    assert outcome.match is expected
    assert outcome.check_status == "ok"


def test_date_comparison_accepts_both_formats() -> None:
    outcome = evaluate_condition(_cond("lte", "31.12.2026"), "date", "2026-10-01", True)
    assert outcome.match is True
    assert evaluate_condition(_cond("gt", "2026-12-31"), "date", "2026-10-01", True).match is False


def test_boolean_equality() -> None:
    assert evaluate_condition(_cond("eq", "да"), "boolean", True, True).match is True
    assert evaluate_condition(_cond("eq", "нет"), "boolean", True, True).match is False


def test_string_eq_and_contains() -> None:
    assert evaluate_condition(_cond("eq", "Отходы бумаги"), "string", "отходов бумаги", True).match
    outcome = evaluate_condition(
        _cond("contains", "утилизац*"), "string", "Сбор и утилизация отходов", True
    )
    assert outcome.match is True


def test_scalar_in_and_not_in_list() -> None:
    options = ["1 11 010 21 49 2", "4 71 101 01 52 1"]
    assert evaluate_condition(_cond("in", options), "string", "11101021492", True).match is True
    outcome = evaluate_condition(_cond("not_in", options), "string", "11101021492", True)
    assert outcome.match is False
    assert outcome.mismatched_values == ["11101021492"]


def test_list_all_in_reports_missing() -> None:
    outcome = evaluate_condition(
        _cond("all_in", ["1 11 010 21 49 2"]), "list", ["11101021492", "4 71 101 01 52 1"], True
    )
    assert outcome.match is False
    assert outcome.mismatched_values == ["4 71 101 01 52 1"]


def test_list_any_in_and_none_in() -> None:
    options = ["отходы бумаги"]
    values = ["отходов бумаги", "лампы ртутные"]
    assert evaluate_condition(_cond("any_in", options), "list", values, True).match is True
    outcome = evaluate_condition(_cond("none_in", options), "list", values, True)
    assert outcome.match is False
    assert outcome.mismatched_values == ["отходов бумаги"]


def test_not_found_is_not_checked() -> None:
    outcome = evaluate_condition(_cond("gte", "5"), "number", None, False)
    assert outcome.match is None
    assert outcome.check_status == "not_found_in_tz"
    assert evaluate_condition(_cond("all_in", ["x1 2"]), "list", [], True).match is None


def test_llm_condition_uses_llm_judgement() -> None:
    cond = {"op": "llm", "value": "не менее 500"}
    assert evaluate_condition(cond, "number", 600.0, True, llm_match=True).match is True
    outcome = evaluate_condition(cond, "number", 600.0, True, llm_match=None)
    assert outcome.match is None
    assert outcome.check_status == "llm_failed"


def test_invalid_value_is_not_checked() -> None:
    outcome = evaluate_condition(_cond("gte", "5"), "number", "много", True)
    assert outcome.match is None
    assert outcome.check_status == "invalid_value"


def test_no_condition() -> None:
    outcome = evaluate_condition(None, "number", 5.0, True)
    assert outcome.match is None
    assert outcome.check_status == "no_condition"


# --- Нормализация полей профиля -------------------------------------------------


def test_normalize_severity_without_condition_is_dropped() -> None:
    [field] = normalize_report_fields([{"id": "f1", "name": "x", "severity": "block"}])
    assert field["condition"] is None
    assert field["severity"] is None
    assert field["type"] == "string"


def test_normalize_list_field_defaults() -> None:
    [field] = normalize_report_fields(
        [
            {
                "id": "f1",
                "name": "коды",
                "type": "list",
                "condition": {"op": "all_in", "value": "1 11 010 21 49 2\n4 71 101 01 52 1"},
            }
        ]
    )
    assert field["extend_list"] is True
    assert field["condition"]["value"] == ["1 11 010 21 49 2", "4 71 101 01 52 1"]
    assert field["condition"]["value_kind"] == "list"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"type": "number", "condition": {"op": "all_in", "value": ["1"]}}, "не применим"),
        ({"type": "number", "condition": {"op": "gte", "value": "много"}}, "не подходит"),
        ({"type": "string", "condition": {"op": "zz", "value": "x"}}, "Неизвестный оператор"),
        ({"type": "list", "condition": {"op": "all_in", "value": []}}, "пуст"),
        ({"type": "string", "condition": {"op": "eq", "value": "12"}}, "слишком короткое"),
        ({"type": "string", "condition": {"op": "contains", "value": "ут*"}}, "3 букв"),
        (
            {"type": "list", "condition": {"op": "all_in", "value_kind": "url", "value": "x"}},
            "адрес http",
        ),
        ({"type": "enum"}, "неизвестный тип"),
    ],
)
def test_normalize_rejects_invalid(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ConditionError, match=message):
        normalize_report_fields([{"id": "f1", "name": "поле", **raw}])


def test_normalize_error_names_the_field() -> None:
    with pytest.raises(ConditionError, match="«объём»"):
        normalize_report_fields(
            [{"id": "f1", "name": "объём", "type": "number", "condition": {"op": "gte"}}]
        )


# --- Пересчёт без LLM -----------------------------------------------------------


def _def(**extra: Any) -> dict[str, Any]:
    return normalize_report_fields([{"id": "f1", "name": "объём", "type": "number", **extra}])[0]


def _stored(field_def: dict[str, Any], **extra: Any) -> dict[str, Any]:
    value = {
        "field_id": "f1",
        "field_name": "объём",
        "field_type": "number",
        "found": True,
        "value": 600.0,
        "extraction_key": extraction_key(field_def),
    }
    return apply_condition({**value, **extra}, field_def)


def test_recompute_applies_changed_code_condition() -> None:
    old = _def(condition={"op": "gte", "value": "500"}, severity="block")
    new = _def(condition={"op": "gte", "value": "700"}, severity="block")
    stored = _stored(old)
    assert stored["match"] is True
    values, complete = recompute_field_values([stored], [new])
    assert complete is True
    assert values[0]["match"] is False
    assert values[0]["condition"]["value"] == "700"
    assert values[0]["value"] == 600.0


def test_recompute_incomplete_when_extraction_changed() -> None:
    old = _def(condition={"op": "gte", "value": "500"})
    new = normalize_report_fields(
        [{"id": "f1", "name": "объём", "hint": "новая подсказка", "type": "number"}]
    )[0]
    values, complete = recompute_field_values([_stored(old)], [new])
    assert complete is False
    assert values[0]["condition"]["value"] == "500"  # оставлено как было


def test_recompute_incomplete_for_new_field_and_drops_deleted() -> None:
    old = _def()
    other = normalize_report_fields([{"id": "f2", "name": "цена", "type": "number"}])[0]
    values, complete = recompute_field_values([_stored(old)], [other])
    assert complete is False
    assert values == []


def test_recompute_llm_condition_kept_if_unchanged_else_needs_reanalysis() -> None:
    llm_def = _def(condition={"op": "llm", "value": "не менее 500"})
    stored = _stored(llm_def, llm_match=True)
    values, complete = recompute_field_values([stored], [llm_def])
    assert complete is True
    assert values[0]["match"] is True

    changed = _def(condition={"op": "llm", "value": "не менее 700"})
    values, complete = recompute_field_values([stored], [changed])
    assert complete is False
    assert values[0]["match"] is None
    assert values[0]["check_status"] == "needs_reanalysis"


def test_recompute_severity_follows_profile() -> None:
    old = _def(condition={"op": "gte", "value": "700"})
    new = _def(condition={"op": "gte", "value": "700"}, severity="soft")
    values, _ = recompute_field_values([_stored(old)], [new])
    assert values[0]["severity"] == "soft"
    assert values[0]["match"] is False


def test_normalize_rejects_unknown_severity() -> None:
    with pytest.raises(ConditionError, match="«объём».*block, soft"):
        normalize_report_fields(
            [
                {
                    "id": "f1",
                    "name": "объём",
                    "type": "number",
                    "condition": {"op": "gte", "value": "5"},
                    "severity": "hard",
                }
            ]
        )
