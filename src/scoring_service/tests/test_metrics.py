"""Тесты метрик и датасета."""

from __future__ import annotations

import json
from pathlib import Path

from scoring_service.eval.dataset import EvalItem, load_dataset, resolve_expected_verdict
from scoring_service.eval.metrics import (
    RegressionThresholds,
    compare_reports,
    compute_classification_metrics,
    compute_consistency,
    compute_metrics,
)


def test_compute_metrics_perfect() -> None:
    m = compute_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert m.mae == 0.0
    assert m.rmse == 0.0
    assert m.accuracy_at_tol == 1.0
    assert m.pearson == 1.0


def test_compute_metrics_error() -> None:
    m = compute_metrics([1.0, 2.0, 3.0], [1.0, 4.0, 3.0], tolerance=1.0)
    assert m.mae == 0.6667
    assert m.accuracy_at_tol == 0.6667


def test_eval_item_clamp() -> None:
    assert EvalItem(description="x", expected_fit=15.0).expected_fit == 10.0


def test_expected_verdict_explicit() -> None:
    item = EvalItem(description="x", expected_fit=8.0, expected_verdict=False)
    assert resolve_expected_verdict(item, accept_threshold=5.0) is False


def test_expected_verdict_from_threshold() -> None:
    assert resolve_expected_verdict(EvalItem(description="x", expected_fit=6.0), 5.0) is True
    assert resolve_expected_verdict(EvalItem(description="x", expected_fit=4.0), 5.0) is False
    assert resolve_expected_verdict(EvalItem(description="x", expected_fit=5.0), 5.0) is True


def test_load_dataset_json(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    path.write_text(json.dumps([{"description": "a", "expected_fit": 5.0}]))
    items = load_dataset(path)
    assert items == [EvalItem(description="a", expected_fit=5.0)]


def test_load_dataset_csv(tmp_path: Path) -> None:
    path = tmp_path / "d.csv"
    path.write_text("description,expected_fit\na,5.0\nb,3.0\n")
    items = load_dataset(path)
    assert len(items) == 2
    assert items[1].expected_fit == 3.0


def test_metrics_has_bias_and_wape() -> None:
    m = compute_metrics([2.0, 4.0], [3.0, 5.0])
    assert m.bias == 1.0  # pred - exp, систематическая переоценка
    assert m.wape == round(2 / 6, 4)  # sum|err| / sum|exp| = 2 / 6


def test_classification_metrics_basic() -> None:
    cm = compute_classification_metrics(
        expected_verdict=[True, False, True, False],
        predicted_verdict=[True, False, True, True],
        expected_fit=[8.0, 2.0, 7.0, 3.0],
        predicted_fit=[9.0, 1.0, 8.0, 6.0],
        k=2,
    )
    assert cm.n == 4
    assert cm.tp == 2
    assert cm.fp == 1
    assert cm.fn == 0
    assert cm.tn == 1
    assert cm.accuracy_binary == 0.75
    assert cm.recall == 1.0
    assert cm.precision == round(2 / 3, 4)
    # top-2 по predicted_fit: индексы 0 и 2 — оба релевантны.
    assert cm.precision_at_k == 1.0


def test_classification_metrics_zero_precision() -> None:
    cm = compute_classification_metrics(
        expected_verdict=[False, False],
        predicted_verdict=[True, True],
        expected_fit=[1.0, 2.0],
        predicted_fit=[9.0, 8.0],
        k=None,
    )
    assert cm.precision == 0.0
    assert cm.recall == 0.0
    assert cm.f1 == 0.0
    assert cm.precision_at_k is None


def test_consistency_perfect_and_instable() -> None:
    perfect = compute_consistency(
        [[8.0, 8.0], [5.0, 5.0]], [["accept", "accept"], ["reject", "reject"]]
    )
    assert perfect.n_repeats == 2
    assert perfect.score_std == 0.0
    assert perfect.verdict_instability == 0.0

    instable = compute_consistency([[8.0, 6.0]], [["accept", "reject"]])
    assert instable.score_std > 0.0
    assert instable.verdict_instability == 1.0


def test_compare_reports_passes_when_equal() -> None:
    base = compute_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    cur = compute_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    cmp = compare_reports(base, cur, RegressionThresholds())
    assert cmp.passed
    assert cmp.mae_delta == 0.0


def test_compare_reports_fails_on_mae_regression() -> None:
    base = compute_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    cur = compute_metrics([1.0, 2.0, 3.0], [1.0, 5.0, 3.0])  # mae 1.0
    cmp = compare_reports(base, cur, RegressionThresholds(max_mae_reg=0.3))
    assert not cmp.passed
    assert cmp.mae_delta > 0.3


def test_compare_reports_fails_on_spearman_drop() -> None:
    base = compute_metrics([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    cur = compute_metrics([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0])  # инверсия
    cmp = compare_reports(base, cur, RegressionThresholds(min_spearman_reg=0.02))
    assert not cmp.passed
    assert cmp.spearman_delta is not None and cmp.spearman_delta < -0.02
