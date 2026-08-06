"""Тесты заглушек P(win) и Margin."""

from __future__ import annotations

from scoring_service.modules import margin as margin_module
from scoring_service.modules import p_win as p_win_module
from scoring_service.settings import Settings


def test_p_win_default() -> None:
    settings = Settings(p_win=1.0)
    assert p_win_module.p_win({"nmck": 500}, settings) == 1.0


def test_margin_is_nmck() -> None:
    settings = Settings()
    assert margin_module.margin({"nmck": 500.0}, settings) == 500.0


def test_margin_applies_rate() -> None:
    settings = Settings(margin_rate=0.5)
    assert margin_module.margin({"nmck": 100.0}, settings) == 50.0
