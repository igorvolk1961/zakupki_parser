"""Валидация точности сервиса скоринга на тестовой выборке.

Сравнивает оценки сервиса скоринга с ground-truth категориями из датасета и
считает метрики по категориям (accuracy, precision/recall по «высоко-привлекательной»).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zakupki_mos_simulator.data.models import CATEGORIES, Dataset

# Категории, считающиеся «высоко-привлекательными» для поставщика.
POSITIVE_CATEGORIES = {"perfect", "synonym", "close"}


@dataclass
class Metrics:
    """Метрики точности скоринга."""

    total: int = 0
    matched: int = 0
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    # Двоичная метрика: высоко-привлекательная (perfect/synonym/close) vs остальное.
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    threshold: float = 0.0

    @property
    def accuracy(self) -> float:
        return self.matched / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        if (self.precision + self.recall) == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "matched": self.matched,
            "accuracy": round(self.accuracy, 4),
            "precision_positive": round(self.precision, 4),
            "recall_positive": round(self.recall, 4),
            "f1_positive": round(self.f1, 4),
            "threshold": self.threshold,
            "confusion": {
                "tp": self.tp,
                "fp": self.fp,
                "tn": self.tn,
                "fn": self.fn,
            },
            "by_category": self.by_category,
        }


def _load_scores(path: str | Path) -> dict[str, float]:
    """Загружает оценки сервиса скоринга: CSV (number,score) или JSON {number: score}."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Файл оценок не найден: {p}")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if text.startswith("{"):
        data = json.loads(text)
        return {str(k): float(v) for k, v in data.items()}
    reader = csv.DictReader(p.open("r", encoding="utf-8"))
    return {row["number"].strip(): float(row["score"]) for row in reader}


def evaluate(
    dataset: Dataset,
    scores: dict[str, float],
    threshold: float = 0.0,
) -> Metrics:
    """Считает метрики, считая категорию совпавшей, если сервис отнёс её к
    той же бинарной группе (высоко-привлекательная / остальное)."""
    metrics = Metrics(total=0, matched=0, threshold=threshold)
    for cat in CATEGORIES:
        metrics.by_category[cat] = {"total": 0, "predicted_positive": 0}
    for p in dataset.procurements:
        score = scores.get(p.number)
        if score is None:
            continue
        metrics.total += 1
        metrics.by_category[p.category]["total"] += 1
        actual_positive = p.category in POSITIVE_CATEGORIES
        predicted_positive = score >= threshold
        if predicted_positive:
            metrics.by_category[p.category]["predicted_positive"] += 1
        if actual_positive == predicted_positive:
            metrics.matched += 1
        if actual_positive and predicted_positive:
            metrics.tp += 1
        elif not actual_positive and predicted_positive:
            metrics.fp += 1
        elif not actual_positive and not predicted_positive:
            metrics.tn += 1
        else:
            metrics.fn += 1
    return metrics


def validate_cli(dataset: Dataset, scores_path: str | Path, threshold: float) -> Metrics:
    """CLI-обёртка: загружает оценки и возвращает метрики."""
    scores = _load_scores(scores_path)
    return evaluate(dataset, scores, threshold)
