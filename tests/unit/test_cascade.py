"""Unit-тесты постадийных уведомлений (api/app.py).

Автокаскад Fit -> P(win) -> Margin убран (ADR: P(win)/Margin — только on-demand
по запросу тендеролога), поэтому тестируется только порог уведомления после стадии.
"""

from __future__ import annotations

from zakupki_parser.api.app import _meets_stage_notify_threshold
from zakupki_parser.config.models import ScoreConfig


class _FakeCfg:
    score: ScoreConfig
    ops: object


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


def _cfg() -> _FakeCfg:
    cfg = _FakeCfg()
    cfg.score = ScoreConfig()
    cfg.ops = object()
    return cfg


class _FakeNotifications:
    notify_min_fit_score: float
    notify_min_pwin: float
    notify_min_margin: float
    notify_fit_enabled: bool
    notify_pwin_enabled: bool
    notify_margin_enabled: bool

    def __init__(
        self,
        fit: float = 0.4,
        pwin: float = 0.0,
        margin: float = 0.0,
        fit_enabled: bool = True,
        pwin_enabled: bool = True,
        margin_enabled: bool = True,
    ) -> None:
        self.notify_min_fit_score = fit
        self.notify_min_pwin = pwin
        self.notify_min_margin = margin
        self.notify_fit_enabled = fit_enabled
        self.notify_pwin_enabled = pwin_enabled
        self.notify_margin_enabled = margin_enabled


class _FakeState:
    def __init__(
        self,
        fit_thr: float = 0.4,
        pwin_thr: float = 0.0,
        margin_thr: float = 0.0,
        fit_enabled: bool = True,
        pwin_enabled: bool = True,
        margin_enabled: bool = True,
    ) -> None:
        self.notify_min_fit_score = fit_thr
        self.cfg = _cfg()
        self.cfg.ops = type(
            "_Ops",
            (),
            {
                "notifications": _FakeNotifications(
                    pwin=pwin_thr,
                    margin=margin_thr,
                    fit_enabled=fit_enabled,
                    pwin_enabled=pwin_enabled,
                    margin_enabled=margin_enabled,
                )
            },
        )()


def test_notify_threshold_fit_above() -> None:
    assert _meets_stage_notify_threshold(_FakeRow("fit", fit_score=0.6), _FakeState()) is True  # type: ignore[arg-type]


def test_notify_threshold_fit_below() -> None:
    assert _meets_stage_notify_threshold(_FakeRow("fit", fit_score=0.2), _FakeState()) is False  # type: ignore[arg-type]


def test_notify_fit_disabled_never_notifies() -> None:
    row = _FakeRow("fit", fit_score=0.9)
    assert _meets_stage_notify_threshold(row, _FakeState(fit_enabled=False)) is False  # type: ignore[arg-type]


def test_notify_threshold_pwin_above() -> None:
    row = _FakeRow("pwin", fit_score=0.6, p_win=0.5)
    assert _meets_stage_notify_threshold(row, _FakeState()) is True  # type: ignore[arg-type]


def test_notify_threshold_pwin_below() -> None:
    row = _FakeRow("pwin", fit_score=0.6, p_win=0.2)
    assert _meets_stage_notify_threshold(row, _FakeState(pwin_thr=0.3)) is False  # type: ignore[arg-type]


def test_notify_threshold_margin_above() -> None:
    row = _FakeRow("margin", fit_score=0.6, p_win=0.5, margin=500_000.0)
    assert _meets_stage_notify_threshold(row, _FakeState()) is True  # type: ignore[arg-type]


def test_notify_threshold_margin_below() -> None:
    row = _FakeRow("margin", fit_score=0.6, p_win=0.5, margin=100_000.0)
    assert _meets_stage_notify_threshold(row, _FakeState(margin_thr=300_000.0)) is False  # type: ignore[arg-type]


def test_notify_pwin_disabled_never_notifies() -> None:
    row = _FakeRow("pwin", fit_score=0.6, p_win=0.9)
    assert _meets_stage_notify_threshold(row, _FakeState(pwin_enabled=False)) is False  # type: ignore[arg-type]


def test_notify_margin_disabled_never_notifies() -> None:
    row = _FakeRow("margin", fit_score=0.6, p_win=0.9, margin=500_000.0)
    assert _meets_stage_notify_threshold(row, _FakeState(margin_enabled=False)) is False  # type: ignore[arg-type]


def test_notify_threshold_sim_not_notified() -> None:
    row = _FakeRow("sim", fit_score=0.0)
    assert _meets_stage_notify_threshold(row, _FakeState()) is False  # type: ignore[arg-type]


def test_manual_and_reject_not_notified() -> None:
    assert _meets_stage_notify_threshold(_FakeRow("manual", fit_score=0.9), _FakeState()) is False  # type: ignore[arg-type]
    assert _meets_stage_notify_threshold(_FakeRow("reject", fit_score=0.1), _FakeState()) is False  # type: ignore[arg-type]
