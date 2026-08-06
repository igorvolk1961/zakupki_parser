"""Тесты дефолтного score (приоритета) — зеркало эвристики парсера."""

from __future__ import annotations

from scoring_transport.scorer import compute_default_score, priority_for
from scoring_transport.settings import Settings


def test_compute_default_score_nmck() -> None:
    settings = Settings(p_win=1.0)
    score = compute_default_score({"okpd2_codes": "62.01", "nmck": 1000.0}, settings)
    assert score == 900.0  # fit 0.9 × 1.0 × 1000


def test_compute_default_score_default_fit() -> None:
    settings = Settings(default_fit=0.5)
    score = compute_default_score({"okpd2_codes": "99.99", "nmck": 100.0}, settings)
    assert score == 50.0


def test_priority_uses_stored_default_score() -> None:
    settings = Settings()
    card = {"score_method": "default", "score": 123.0, "nmck": 1.0}
    assert priority_for(card, settings) == 123.0


def test_priority_recomputes_without_score() -> None:
    settings = Settings(p_win=1.0)
    card = {"score_method": "external", "score": 999.0, "okpd2_codes": "62.01", "nmck": 100.0}
    assert priority_for(card, settings) == 90.0
