"""Unit-тесты округления score и клиентской активности закупки."""

from __future__ import annotations

from datetime import UTC, datetime

from zakupki_parser.storage.repository import _round_score, effective_is_active


def test_round_score_to_cents() -> None:
    assert _round_score(12.345678) == 12.35
    assert _round_score(100.0) == 100.0
    assert _round_score(0.004) == 0.0


def test_round_score_none() -> None:
    assert _round_score(None) is None


def test_effective_is_active_by_status() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    assert effective_is_active(False, None, now) is False
    assert effective_is_active(False, datetime(2026, 8, 20, 12, 0, tzinfo=UTC), now) is False


def test_effective_is_active_without_deadline() -> None:
    assert effective_is_active(True, None, datetime(2026, 8, 10, 12, 0, tzinfo=UTC)) is True


def test_effective_is_active_considers_deadline() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    future = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    expired = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert effective_is_active(True, future, now) is True
    assert effective_is_active(True, expired, now) is False
