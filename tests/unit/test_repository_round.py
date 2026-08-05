"""Unit-тесты округления score перед записью в БД."""

from __future__ import annotations

from zakupki_parser.storage.repository import _round_score


def test_round_score_to_cents() -> None:
    assert _round_score(12.345678) == 12.35
    assert _round_score(100.0) == 100.0
    assert _round_score(0.004) == 0.0


def test_round_score_none() -> None:
    assert _round_score(None) is None
