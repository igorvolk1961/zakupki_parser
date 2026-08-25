"""Непрерывные метрики: регрессия, усреднение по повторам, consistency, regression-гейт."""

from __future__ import annotations

import math
import statistics

from scoring_service.eval.metrics.models import (
    ConsistencyMetrics,
    Metrics,
    MetricsStats,
    RegressionThresholds,
    ReportComparison,
)


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    if den == 0:
        return None
    return num / den


def _spearman(x: list[float], y: list[float]) -> float | None:
    def _rank(values: list[float]) -> list[float]:
        indexed = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        for pos, idx in enumerate(indexed):
            ranks[idx] = float(pos + 1)
        return ranks

    return _pearson(_rank(x), _rank(y))


def compute_metrics(
    expected: list[float],
    predicted: list[float],
    tolerance: float = 1.0,
) -> Metrics:
    """Рассчитать непрерывные метрики по спискам ожидаемых и предсказанных скоров."""
    n = len(expected)
    if n == 0:
        return Metrics(
            n=0,
            mae=0.0,
            rmse=0.0,
            accuracy_at_tol=0.0,
            pearson=None,
            spearman=None,
            tolerance=tolerance,
            bias=0.0,
            wape=0.0,
        )
    errors = [p - e for p, e in zip(predicted, expected, strict=True)]
    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)
    within = sum(1 for e in errors if abs(e) <= tolerance) / n
    bias = sum(errors) / n
    denom = sum(abs(e) for e in expected)
    wape = sum(abs(e) for e in errors) / denom if denom else 0.0
    return Metrics(
        n=n,
        mae=round(mae, 4),
        rmse=round(rmse, 4),
        accuracy_at_tol=round(within, 4),
        pearson=_pearson(expected, predicted),
        spearman=_spearman(expected, predicted),
        tolerance=tolerance,
        bias=round(bias, 4),
        wape=round(wape, 4),
    )


def _std(xs: list[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def _mean_optional(xs: list[float | None]) -> float | None:
    present = [x for x in xs if x is not None]
    if not present:
        return None
    return statistics.fmean(present)


def _std_optional(xs: list[float | None]) -> float | None:
    present = [x for x in xs if x is not None]
    if not present:
        return None
    return _std(present)


def mean_metrics(metrics: list[Metrics]) -> Metrics:
    """Усреднить непрерывные метрики по повторам (по первому берём n и tolerance)."""
    first = metrics[0]
    return Metrics(
        n=first.n,
        mae=round(statistics.fmean(m.mae for m in metrics), 4),
        rmse=round(statistics.fmean(m.rmse for m in metrics), 4),
        accuracy_at_tol=round(statistics.fmean(m.accuracy_at_tol for m in metrics), 4),
        pearson=_mean_optional([m.pearson for m in metrics]),
        spearman=_mean_optional([m.spearman for m in metrics]),
        tolerance=first.tolerance,
        bias=round(statistics.fmean(m.bias for m in metrics), 4),
        wape=round(statistics.fmean(m.wape for m in metrics), 4),
    )


def metrics_stats(metrics: list[Metrics]) -> MetricsStats:
    """Разброс непрерывных метрик по повторам."""
    return MetricsStats(
        mean_mae=round(statistics.fmean(m.mae for m in metrics), 4),
        std_mae=round(_std([m.mae for m in metrics]), 4),
        mean_rmse=round(statistics.fmean(m.rmse for m in metrics), 4),
        std_rmse=round(_std([m.rmse for m in metrics]), 4),
        mean_accuracy_at_tol=round(statistics.fmean(m.accuracy_at_tol for m in metrics), 4),
        std_accuracy_at_tol=round(_std([m.accuracy_at_tol for m in metrics]), 4),
        mean_spearman=_mean_optional([m.spearman for m in metrics]),
        std_spearman=_std_optional([m.spearman for m in metrics]),
        mean_bias=round(statistics.fmean(m.bias for m in metrics), 4),
        std_bias=round(_std([m.bias for m in metrics]), 4),
        mean_wape=round(statistics.fmean(m.wape for m in metrics), 4),
        std_wape=round(_std([m.wape for m in metrics]), 4),
    )


def compute_consistency(
    repeated_scores: list[list[float]],
    repeated_verdicts: list[list[str]],
) -> ConsistencyMetrics:
    """Стабильность при повторах: средний std скора и доля нестабильных verdict.

    ``repeated_scores[i]`` — N значений ``final_fit_score`` для i-го примера;
    ``repeated_verdicts[i]`` — N значений verdict (accept/reject) для i-го примера.
    """
    if not repeated_scores:
        return ConsistencyMetrics(n_repeats=0, score_std=0.0, verdict_instability=0.0)
    n_repeats = len(repeated_scores[0])
    stds = [statistics.stdev(s) if len(s) > 1 else 0.0 for s in repeated_scores]
    score_std = statistics.fmean(stds) if stds else 0.0
    unstable = sum(1 for v in repeated_verdicts if len(set(v)) > 1)
    instability = unstable / len(repeated_verdicts) if repeated_verdicts else 0.0
    return ConsistencyMetrics(
        n_repeats=n_repeats,
        score_std=round(score_std, 4),
        verdict_instability=round(instability, 4),
    )


def compare_reports(
    baseline: Metrics,
    current: Metrics,
    thresholds: RegressionThresholds,
) -> ReportComparison:
    """Regression-гейт: сравнить текущие непрерывные метрики с baseline.

    Деградация считается fail, если текущая метрика ухудшилась сильнее порога:
    ошибки (MAE/RMSE) выросли, accuracy@tol упал, Spearman упал.
    """
    mae_delta = current.mae - baseline.mae
    rmse_delta = current.rmse - baseline.rmse
    accuracy_delta = current.accuracy_at_tol - baseline.accuracy_at_tol
    spearman_delta: float | None = None
    if baseline.spearman is not None and current.spearman is not None:
        spearman_delta = current.spearman - baseline.spearman

    failed = (
        mae_delta > thresholds.max_mae_reg
        or rmse_delta > thresholds.max_rmse_reg
        or accuracy_delta < -thresholds.max_acc_reg
        or (spearman_delta is not None and spearman_delta < -thresholds.min_spearman_reg)
    )
    return ReportComparison(
        mae_delta=round(mae_delta, 4),
        rmse_delta=round(rmse_delta, 4),
        accuracy_delta=round(accuracy_delta, 4),
        spearman_delta=round(spearman_delta, 4) if spearman_delta is not None else None,
        passed=not failed,
    )
