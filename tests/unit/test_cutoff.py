"""Unit-тесты логики выхода из цикла по порогу даты.

Покрывают сценарий «перезапуск на следующий день» при датах без времени
(на площадке обычно указана только дата).
"""

from __future__ import annotations

from datetime import UTC, datetime

from zakupki_parser.parser.cutoff import is_older_than_cutoff
from zakupki_parser.parser.json_utils import json_safe


def test_same_day_not_older() -> None:
    # порог — середина дня 1 (прошлая сессия), запись того же дня (даже вечером).
    # `extract_update_date` уже нормализует дату в ISO (handler_date_iso),
    # поэтому helper получает ISO-представление "06.08.2026" -> "2026-08-06T00:00:00".
    cutoff = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert is_older_than_cutoff("2026-08-06T00:00:00", cutoff) is False


def test_next_day_not_older() -> None:
    cutoff = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert is_older_than_cutoff("2026-08-07", cutoff) is False


def test_previous_day_is_older() -> None:
    cutoff = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert is_older_than_cutoff("2026-08-05", cutoff) is True


def test_same_day_midnight_not_older() -> None:
    # критичный случай: полночь того же дня, что и порог — НЕ старее
    cutoff = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert is_older_than_cutoff("2026-08-06T00:00:00", cutoff) is False


def test_bad_date_returns_none() -> None:
    cutoff = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert is_older_than_cutoff("garbage", cutoff) is None


def test_naive_vs_aware_comparison_safe() -> None:
    # naive-дата записи (без tz) сравнивается корректно, без TypeError
    cutoff = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert is_older_than_cutoff("2026-08-06T00:00:00", cutoff) is False


def test_datetime_input() -> None:
    cutoff = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert is_older_than_cutoff(datetime(2026, 8, 5, 23, 0, tzinfo=UTC), cutoff) is True
    assert is_older_than_cutoff(datetime(2026, 8, 6, 9, 0, tzinfo=UTC), cutoff) is False
    assert is_older_than_cutoff(None, cutoff) is None


def test_json_safe_datetime() -> None:
    dt = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)
    data = {"deadline": dt, "nested": [dt], "ok": 1}
    safe = json_safe(data)
    assert safe["deadline"] == dt.isoformat()
    assert safe["nested"][0] == dt.isoformat()
    assert safe["ok"] == 1
