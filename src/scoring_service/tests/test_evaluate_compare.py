"""Regression-гейт CLI: код выхода `evaluate` зависит от `ReportComparison.passed`."""

from __future__ import annotations

from pathlib import Path

import pytest

from scoring_service.cli import _cmd_evaluate
from scoring_service.eval.metrics import (
    EvaluationReport,
    Metrics,
    ReportComparison,
)
from scoring_service.settings import Settings


def _report(mae: float) -> EvaluationReport:
    return EvaluationReport(
        n=3,
        continuous=Metrics(
            n=3,
            mae=mae,
            rmse=0.0,
            accuracy_at_tol=1.0,
            pearson=1.0,
            spearman=1.0,
            tolerance=1.0,
            bias=0.0,
            wape=0.0,
        ),
    )


def _fake_evaluate_cli(
    *args: object, **kwargs: object
) -> tuple[EvaluationReport, ReportComparison]:
    del args, kwargs
    return _report(1.0), _STATE.comparison


class _State:
    def __init__(self) -> None:
        self.comparison: ReportComparison = ReportComparison(
            mae_delta=0.0,
            rmse_delta=0.0,
            accuracy_delta=0.0,
            spearman_delta=0.0,
            passed=True,
        )


_STATE = _State()


def _call_cmd_evaluate() -> int:
    return _cmd_evaluate(
        Settings(score_use_stub=True),
        Path("x.json"),
        None,
        None,
        1.0,
        5.0,
        None,
        1,
        Path("baseline.json"),
        0.3,
        0.4,
        0.03,
        0.02,
    )


def test_cmd_evaluate_returns_zero_when_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    from scoring_service.eval import evaluate

    _STATE.comparison = _STATE.comparison.model_copy(update={"passed": True})
    monkeypatch.setattr(evaluate, "evaluate_cli", _fake_evaluate_cli)
    assert _call_cmd_evaluate() == 0


def test_cmd_evaluate_returns_one_when_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    from scoring_service.eval import evaluate

    _STATE.comparison = _STATE.comparison.model_copy(update={"passed": False})
    monkeypatch.setattr(evaluate, "evaluate_cli", _fake_evaluate_cli)
    assert _call_cmd_evaluate() == 1
