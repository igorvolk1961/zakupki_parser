"""Бинарные (классификационные) метрики решения судьи и их усреднение."""

from __future__ import annotations

import statistics

from scoring_service.eval.metrics.models import ClassificationMetrics, ClassificationStats
from scoring_service.eval.metrics.regression import _mean_optional, _std


def compute_classification_metrics(
    expected_verdict: list[bool],
    predicted_verdict: list[bool],
    expected_fit: list[float],
    predicted_fit: list[float],
    k: int | None = None,
) -> ClassificationMetrics:
    """Бинарные метрики решения судьи (True = accept) против ожидаемой метки.

    ``precision_at_k`` считает долю релевантных (expected_verdict) среди top-K
    примеров по ``predicted_fit`` (убыванием). Если ``k`` не задан или равен 0 —
    не считается.
    """
    n = len(expected_verdict)
    tp = fp = tn = fn = 0
    for exp, pred in zip(expected_verdict, predicted_verdict, strict=True):
        if pred:
            if exp:
                tp += 1
            else:
                fp += 1
        else:
            if exp:
                fn += 1
            else:
                tn += 1

    accuracy_binary = (tp + tn) / n if n else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    precision_at_k: float | None = None
    if k and k > 0 and n > 0:
        order = sorted(range(n), key=lambda i: predicted_fit[i], reverse=True)
        top = order[: min(k, n)]
        relevant = sum(1 for i in top if expected_verdict[i])
        precision_at_k = relevant / len(top)

    return ClassificationMetrics(
        n=n,
        k=k,
        accuracy_binary=round(accuracy_binary, 4),
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        precision_at_k=round(precision_at_k, 4) if precision_at_k is not None else None,
    )


def mean_classification(classification: list[ClassificationMetrics]) -> ClassificationMetrics:
    """Усреднить бинарные метрики по повторам; confusion — по первому повтору."""
    first = classification[0]
    return ClassificationMetrics(
        n=first.n,
        k=first.k,
        accuracy_binary=round(statistics.fmean(c.accuracy_binary for c in classification), 4),
        precision=round(statistics.fmean(c.precision for c in classification), 4),
        recall=round(statistics.fmean(c.recall for c in classification), 4),
        f1=round(statistics.fmean(c.f1 for c in classification), 4),
        tp=first.tp,
        fp=first.fp,
        tn=first.tn,
        fn=first.fn,
        precision_at_k=_mean_optional([c.precision_at_k for c in classification]),
    )


def classification_stats(classification: list[ClassificationMetrics]) -> ClassificationStats:
    """Разброс бинарных метрик по повторам."""
    return ClassificationStats(
        mean_accuracy=round(statistics.fmean(c.accuracy_binary for c in classification), 4),
        std_accuracy=round(_std([c.accuracy_binary for c in classification]), 4),
        mean_precision=round(statistics.fmean(c.precision for c in classification), 4),
        std_precision=round(_std([c.precision for c in classification]), 4),
        mean_recall=round(statistics.fmean(c.recall for c in classification), 4),
        std_recall=round(_std([c.recall for c in classification]), 4),
        mean_f1=round(statistics.fmean(c.f1 for c in classification), 4),
        std_f1=round(_std([c.f1 for c in classification]), 4),
    )
