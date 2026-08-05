"""Unit-тесты скоринга закупок."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from zakupki_parser.config.models import ScoreConfig
from zakupki_parser.scoring import (
    ExternalScoreClient,
    compute_default_score,
    score_for_record,
)


def test_default_score_formula() -> None:
    cfg = ScoreConfig(method="default", fit_table={"62.01": 0.9}, p_win=1.0)
    record = {"okpd2_codes": "62.01", "nmck": 1000.0}
    assert compute_default_score(record, cfg) == pytest.approx(900.0)


def test_default_score_unknown_code_uses_default_fit() -> None:
    cfg = ScoreConfig(default_fit=0.5)
    assert compute_default_score({"okpd2_codes": "99.99", "nmck": 200.0}, cfg) == 100.0


def test_default_score_fit_by_ancestor_prefix() -> None:
    # точного кода "62.01.29.000" нет, но есть предок "62.01" -> fit=0.9
    cfg = ScoreConfig(fit_table={"62.01": 0.9}, default_fit=0.5)
    record = {"okpd2_codes": "62.01.29.000", "nmck": 1000.0}
    assert compute_default_score(record, cfg) == pytest.approx(900.0)


def test_default_score_no_nmck_zero() -> None:
    cfg = ScoreConfig(fit_table={"62.01": 0.9})
    assert compute_default_score({"okpd2_codes": "62.01"}, cfg) == 0.0


def test_default_score_rounded_to_cents() -> None:
    # Точность score в БД — не более 0.01 ₽
    cfg = ScoreConfig(fit_table={"62.01": 0.1}, p_win=1.0)
    assert compute_default_score({"okpd2_codes": "62.01", "nmck": 1234.567}, cfg) == 123.46


@pytest.mark.asyncio
async def test_score_for_record_default_method() -> None:
    cfg = ScoreConfig(method="default", fit_table={"62.01": 0.9})
    score, method = await score_for_record({"okpd2_codes": "62.01", "nmck": 100.0}, cfg, None)
    assert score == pytest.approx(90.0)
    assert method == "default"


@pytest.mark.asyncio
async def test_external_before_save_success() -> None:
    cfg = ScoreConfig(method="external", external_call_mode="before_save")

    class FakeClient:
        async def score(self, record: dict[str, Any]) -> float:
            return 42.0

    score, method = await score_for_record({"nmck": 1.0}, cfg, FakeClient())  # type: ignore[arg-type]
    assert score == 42.0
    assert method == "external"


@pytest.mark.asyncio
async def test_external_before_save_failure_falls_back() -> None:
    cfg = ScoreConfig(method="external", external_call_mode="before_save", fit_table={"62": 0.5})

    class FakeClient:
        async def score(self, record: dict[str, Any]) -> float:
            raise RuntimeError("external down")

    score, method = await score_for_record({"okpd2_codes": "62", "nmck": 100.0}, cfg, FakeClient())  # type: ignore[arg-type]
    assert score == pytest.approx(50.0)
    assert method == "default"


def test_external_client_requires_url() -> None:
    with pytest.raises(ValueError):
        client = ExternalScoreClient(ScoreConfig(method="external"))
        import asyncio

        asyncio.run(client.score({"nmck": 1.0}))


@pytest.mark.asyncio
async def test_deadline_expired_score_zero() -> None:
    cfg = ScoreConfig(method="default", fit_table={"62.01": 0.9})
    now = datetime(2026, 8, 5, tzinfo=UTC)
    record = {
        "okpd2_codes": "62.01",
        "nmck": 1000.0,
        "deadline": datetime(2026, 8, 1, tzinfo=UTC),  # уже прошёл
    }
    score, method = await score_for_record(record, cfg, None, now)
    assert score == 0.0
    assert method == "deadline_expired"


@pytest.mark.asyncio
async def test_deadline_future_uses_normal_score() -> None:
    cfg = ScoreConfig(method="default", fit_table={"62.01": 0.9})
    now = datetime(2026, 8, 5, tzinfo=UTC)
    record = {
        "okpd2_codes": "62.01",
        "nmck": 1000.0,
        "deadline": datetime(2026, 8, 10, tzinfo=UTC),
    }
    score, method = await score_for_record(record, cfg, None, now)
    assert score == pytest.approx(900.0)
    assert method == "default"
