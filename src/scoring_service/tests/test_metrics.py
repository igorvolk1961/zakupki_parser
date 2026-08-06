"""Тесты метрик и датасета."""

from __future__ import annotations

import json
from pathlib import Path

from scoring_service.eval.dataset import EvalItem, load_dataset
from scoring_service.eval.metrics import compute_metrics


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
