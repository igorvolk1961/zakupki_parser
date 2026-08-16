"""Unit-тесты оркестрации каскада скоринга Fit -> P(win) -> Margin (api/app.py)."""

from __future__ import annotations

from zakupki_parser.api.app import _is_terminal_stage, _next_stage_score
from zakupki_parser.config.models import ScoreConfig


class _FakeCfg:
    score: ScoreConfig


class _FakeRow:
    def __init__(
        self,
        score_method: str | None,
        fit_score: float | None = None,
        score: float | None = None,
        p_win: float | None = None,
        margin: float | None = None,
    ) -> None:
        self.score_method = score_method
        self.fit_score = fit_score
        self.score = score
        self.p_win = p_win
        self.margin = margin


def _cfg(
    pwin_threshold: float = 0.5,
    margin_threshold: float = 0.6,
    pwin_enabled: bool = True,
    margin_enabled: bool = True,
) -> _FakeCfg:
    cfg = _FakeCfg()
    cfg.score = ScoreConfig(
        pwin_fit_threshold=pwin_threshold,
        margin_threshold=margin_threshold,
        pwin_enabled=pwin_enabled,
        margin_enabled=margin_enabled,
    )
    return cfg


def test_next_stage_fit_above_threshold() -> None:
    row = _FakeRow("fit", fit_score=0.7, score=0.7)
    assert _next_stage_score(row, _cfg()) == ("pwin", 0.7)


def test_next_stage_fit_below_threshold() -> None:
    row = _FakeRow("fit", fit_score=0.4, score=0.4)
    assert _next_stage_score(row, _cfg()) is None


def test_next_stage_fit_above_threshold_pwin_disabled() -> None:
    row = _FakeRow("fit", fit_score=0.7, score=0.7)
    assert _next_stage_score(row, _cfg(pwin_enabled=False)) is None


def test_next_stage_pwin_above_threshold() -> None:
    row = _FakeRow("pwin", fit_score=0.7, p_win=0.5, score=0.35)
    assert _next_stage_score(row, _cfg(margin_threshold=0.3)) == ("margin", 0.35)


def test_next_stage_pwin_below_threshold() -> None:
    row = _FakeRow("pwin", fit_score=0.7, p_win=0.5, score=0.35)
    assert _next_stage_score(row, _cfg(margin_threshold=0.6)) is None


def test_next_stage_pwin_above_threshold_margin_disabled() -> None:
    row = _FakeRow("pwin", fit_score=0.7, p_win=0.5, score=0.35)
    assert _next_stage_score(row, _cfg(margin_threshold=0.3, margin_enabled=False)) is None


def test_next_stage_margin_has_no_next() -> None:
    row = _FakeRow("margin", fit_score=0.7, p_win=0.5, margin=200.0, score=70.0)
    assert _next_stage_score(row, _cfg()) is None


def test_terminal_fit_below_threshold() -> None:
    row = _FakeRow("fit", fit_score=0.4)
    assert _is_terminal_stage(row, _cfg()) is True


def test_terminal_fit_above_threshold_not_terminal() -> None:
    row = _FakeRow("fit", fit_score=0.7)
    assert _is_terminal_stage(row, _cfg()) is False


def test_terminal_fit_above_threshold_pwin_disabled() -> None:
    row = _FakeRow("fit", fit_score=0.7)
    assert _is_terminal_stage(row, _cfg(pwin_enabled=False)) is True


def test_terminal_pwin_below_threshold() -> None:
    row = _FakeRow("pwin", score=0.35)
    assert _is_terminal_stage(row, _cfg(margin_threshold=0.6)) is True


def test_terminal_pwin_above_threshold_not_terminal() -> None:
    row = _FakeRow("pwin", score=0.35)
    assert _is_terminal_stage(row, _cfg(margin_threshold=0.3)) is False


def test_terminal_pwin_above_threshold_margin_disabled() -> None:
    row = _FakeRow("pwin", score=0.35)
    assert _is_terminal_stage(row, _cfg(margin_threshold=0.3, margin_enabled=False)) is True


def test_terminal_margin_always_terminal() -> None:
    row = _FakeRow("margin", score=70.0)
    assert _is_terminal_stage(row, _cfg()) is True
