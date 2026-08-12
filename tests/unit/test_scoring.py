"""Unit-тесты скоринга закупок."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from zakupki_parser.config.models import ScoreConfig
from zakupki_parser.scoring import (
    ScoringTransportClient,
    compute_default_fit,
    compute_default_score,
    score_for_record,
)


def test_default_score_formula() -> None:
    cfg = ScoreConfig(fit_table={"62.01": 0.9}, p_win=1.0)
    record = {"okpd2_codes": "62.01", "nmck": 1000.0}
    assert compute_default_score(record, cfg) == pytest.approx(900.0)
    assert compute_default_fit(record, cfg) == pytest.approx(0.9)


def test_default_score_unknown_code_uses_default_fit() -> None:
    cfg = ScoreConfig(default_fit=0.5)
    assert compute_default_score({"okpd2_codes": "99.99", "nmck": 200.0}, cfg) == 100.0
    assert compute_default_fit({"okpd2_codes": "99.99"}, cfg) == 0.5


def test_default_score_empty_code_uses_empty_code_fit() -> None:
    # Пустой код ОКПД2 -> fit = empty_code_fit (1.0), а не default_fit (0.5).
    cfg = ScoreConfig(default_fit=0.5, empty_code_fit=1.0)
    assert compute_default_fit({"okpd2_codes": ""}, cfg) == 1.0
    assert compute_default_fit({"okpd2_codes": "  "}, cfg) == 1.0
    assert compute_default_fit({}, cfg) == 1.0
    assert compute_default_score({"okpd2_codes": "", "nmck": 200.0}, cfg) == 200.0


def test_default_score_empty_code_overrides_config_fit() -> None:
    # Пустой код — не unknown-код: применяется empty_code_fit, даже если код
    # не в fit_table. По умолчанию empty_code_fit = 1.0.
    cfg = ScoreConfig()
    assert cfg.default_fit == 0.5
    assert cfg.empty_code_fit == 1.0
    assert compute_default_fit({"okpd2_codes": ""}, cfg) == 1.0


def test_default_score_fit_by_ancestor_prefix() -> None:
    # точного кода "62.01.29.000" нет, но есть предок "62.01" -> fit=0.9
    cfg = ScoreConfig(fit_table={"62.01": 0.9}, default_fit=0.5)
    record = {"okpd2_codes": "62.01.29.000", "nmck": 1000.0}
    assert compute_default_score(record, cfg) == pytest.approx(900.0)
    assert compute_default_fit(record, cfg) == pytest.approx(0.9)


def test_default_score_no_nmck_zero() -> None:
    cfg = ScoreConfig(fit_table={"62.01": 0.9})
    assert compute_default_score({"okpd2_codes": "62.01"}, cfg) == 0.0


def test_default_score_margin_rate_applied() -> None:
    # Margin = НМЦК × margin_rate (норма прибыли)
    cfg = ScoreConfig(fit_table={"62.01": 0.9}, p_win=1.0, margin_rate=1.2)
    record = {"okpd2_codes": "62.01", "nmck": 1000.0}
    assert compute_default_score(record, cfg) == pytest.approx(1080.0)


def test_default_score_rounded_to_cents() -> None:
    # Точность score в БД — не более 0.01 ₽
    cfg = ScoreConfig(fit_table={"62.01": 0.1}, p_win=1.0)
    assert compute_default_score({"okpd2_codes": "62.01", "nmck": 1234.567}, cfg) == 123.46


@pytest.mark.asyncio
async def test_score_for_record_default_method() -> None:
    cfg = ScoreConfig(fit_table={"62.01": 0.9})
    score, fit, method = await score_for_record({"okpd2_codes": "62.01", "nmck": 100.0}, cfg)
    assert score == pytest.approx(90.0)
    assert fit == pytest.approx(0.9)
    assert method == "default"


@pytest.mark.asyncio
async def test_deadline_expired_score_zero() -> None:
    cfg = ScoreConfig(fit_table={"62.01": 0.9})
    now = datetime(2026, 8, 5, tzinfo=UTC)
    record = {
        "okpd2_codes": "62.01",
        "nmck": 1000.0,
        "deadline": datetime(2026, 8, 1, tzinfo=UTC),  # уже прошёл
    }
    score, fit, method = await score_for_record(record, cfg, now)
    assert score == 0.0
    assert fit == pytest.approx(0.9)
    assert method == "deadline_expired"


@pytest.mark.asyncio
async def test_deadline_expired_ignored_when_active_only_false() -> None:
    # Поиск по ВСЕМ закупкам: просроченные не помечаются deadline_expired,
    # метод всегда default — чтобы закупка доехала до скоринга и уведомления.
    cfg = ScoreConfig(fit_table={"62.01": 0.9})
    now = datetime(2026, 8, 5, tzinfo=UTC)
    record = {
        "okpd2_codes": "62.01",
        "nmck": 1000.0,
        "deadline": datetime(2026, 8, 1, tzinfo=UTC),  # уже прошёл
    }
    score, fit, method = await score_for_record(record, cfg, now, active_only=False)
    assert score == pytest.approx(900.0)
    assert fit == pytest.approx(0.9)
    assert method == "default"


@pytest.mark.asyncio
async def test_deadline_expired_kept_when_active_only_true() -> None:
    cfg = ScoreConfig(fit_table={"62.01": 0.9})
    now = datetime(2026, 8, 5, tzinfo=UTC)
    record = {
        "okpd2_codes": "62.01",
        "nmck": 1000.0,
        "deadline": datetime(2026, 8, 1, tzinfo=UTC),  # уже прошёл
    }
    score, fit, method = await score_for_record(record, cfg, now, active_only=True)
    assert score == 0.0
    assert fit == pytest.approx(0.9)
    assert method == "deadline_expired"


@pytest.mark.asyncio
async def test_deadline_future_uses_normal_score() -> None:
    cfg = ScoreConfig(fit_table={"62.01": 0.9})
    now = datetime(2026, 8, 5, tzinfo=UTC)
    record = {
        "okpd2_codes": "62.01",
        "nmck": 1000.0,
        "deadline": datetime(2026, 8, 10, tzinfo=UTC),
    }
    score, fit, method = await score_for_record(record, cfg, now)
    assert score == pytest.approx(900.0)
    assert fit == pytest.approx(0.9)
    assert method == "default"


@pytest.mark.asyncio
async def test_transport_client_posts_job() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url).encode()
        captured["json"] = request.content
        return httpx.Response(202, json={"status": "enqueued"})

    transport = httpx.MockTransport(_handler)
    client = ScoringTransportClient("http://localhost:8200")
    await client.enqueue(42, 900.0, transport=transport)

    assert captured["url"] == b"http://localhost:8200/api/scoring/jobs"
    assert b'"procurement_id":42' in captured["json"]
    assert b'"priority":900.0' in captured["json"]
