"""Unit-тесты восстановления структуры таблиц (scoring_common.tables)."""

from __future__ import annotations

from scoring_common.tables import _merge_rows, _split_trailing_marker, _table_md


def test_split_trailing_marker_into_own_cell() -> None:
    assert _split_trailing_marker(["13", "…услуг. Не установлено"]) == [
        "13",
        "…услуг.",
        "Не установлено",
    ]


def test_split_trailing_marker_number_independent() -> None:
    # единственное/множественное число матчится одной формой маркера.
    assert _split_trailing_marker(["5", "Лот № 1: Не применяются"]) == [
        "5",
        "Лот № 1:",
        "Не применяются",
    ]
    assert _split_trailing_marker(["14.1", "…(ПП РФ № 2571): Не требуются"])[2] == "Не требуются"


def test_split_trailing_marker_keeps_standalone_value() -> None:
    # ячейка целиком равна маркеру — не порождаем пустую ячейку слева.
    assert _split_trailing_marker(["8.2", "Организациям инвалидов", "Не предоставляются"]) == [
        "8.2",
        "Организациям инвалидов",
        "Не предоставляются",
    ]


def test_split_trailing_marker_no_match() -> None:
    assert _split_trailing_marker(["11.2", "Непроведение ликвидации участника."]) == [
        "11.2",
        "Непроведение ликвидации участника.",
    ]


def test_merge_rows_vertical_continuation() -> None:
    rows = [["13", "Требования…услуг."], ["", "Не установлено"]]
    assert _merge_rows(rows) == [["13", "Требования…услуг. Не установлено"]]


def test_merge_rows_keeps_distinct_rows() -> None:
    rows = [["11.2", "A"], ["11.3", "B"]]
    assert _merge_rows(rows) == rows


def test_table_md_produces_valid_pipe_table() -> None:
    md = _table_md([["13", "Требования…услуг.", ""], ["11.2", "Непроведение ликвидации."]])
    assert md.count("\n") == 2  # header + separator + data
    assert md.splitlines()[0].startswith("| 13 |")
    assert md.splitlines()[1] == "| --- | --- | --- |"
