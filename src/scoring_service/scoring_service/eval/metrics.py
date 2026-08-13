"""Метрики качества оценки Fit на тестовом наборе.

Непрерывные метрики (``compute_metrics``) сравнивают предсказанные и ожидаемые
скоры; классификационные (``compute_classification_metrics``) — решения судьи
(accept/reject) с бинарной меткой; ``compute_consistency`` оценивает стабильность
пайплайна при повторных прогонах; ``compare_reports`` — regression-гейт при деплое.
"""

from __future__ import annotations

import math
import statistics

from pydantic import BaseModel, Field


class Metrics(BaseModel):
    """Набор непрерывных метрик сравнения предсказанных и ожидаемых скоров."""

    n: int
    mae: float = Field(description="средняя абсолютная ошибка")
    rmse: float = Field(description="корень из средней квадратичной ошибки")
    accuracy_at_tol: float = Field(description="доля в допуске tol")
    pearson: float | None = Field(description="корреляция Пирсона")
    spearman: float | None = Field(description="корреляция Спирмена")
    tolerance: float = Field(description="допуск для accuracy")
    bias: float = Field(description="средняя ошибка (pred - exp); >0 = переоценка")
    wape: float = Field(description="взвешенная абсолютная процентная ошибка")


class ClassificationMetrics(BaseModel):
    """Бинарные метрики решения судьи (accept/reject) против ожидаемой метки."""

    n: int
    k: int | None = Field(description="K для precision@K; None = не считали")
    accuracy_binary: float = Field(description="доля верно предсказанных решений")
    precision: float = Field(description="точность для класса accept")
    recall: float = Field(description="полнота для класса accept")
    f1: float = Field(description="F1 для класса accept")
    tp: int
    fp: int
    tn: int
    fn: int
    precision_at_k: float | None = Field(
        description="доля релевантных среди top-K по predicted_fit; None = не считали"
    )


class ConsistencyMetrics(BaseModel):
    """Стабильность пайплайна при повторных прогонах одного примера."""

    n_repeats: int
    score_std: float = Field(description="средний std final_fit_score по примерам")
    verdict_instability: float = Field(
        description="доля примеров, у которых verdict сменился при повторах"
    )


class MetricsStats(BaseModel):
    """Разброс непрерывных метрик по повторам (mean ± std)."""

    mean_mae: float
    std_mae: float
    mean_rmse: float
    std_rmse: float
    mean_accuracy_at_tol: float
    std_accuracy_at_tol: float
    mean_spearman: float | None
    std_spearman: float | None
    mean_bias: float
    std_bias: float
    mean_wape: float
    std_wape: float


class ClassificationStats(BaseModel):
    """Разброс бинарных метрик по повторам (mean ± std)."""

    mean_accuracy: float
    std_accuracy: float
    mean_precision: float
    std_precision: float
    mean_recall: float
    std_recall: float
    mean_f1: float
    std_f1: float


class EvaluationReport(BaseModel):
    """Полный отчёт офлайн-оценки: непрерывные + классификационные + (опц.) консистентность.

    ``continuous``/``classification`` — «головные» значения: при ``repetitions>1`` это
    средние по повторам, ``continuous_stats``/``classification_stats`` — разброс (mean±std).
    Confusion-счётчики в агрегированной классификации показаны по первому повтору.
    """

    n: int
    continuous: Metrics
    classification: ClassificationMetrics | None = None
    consistency: ConsistencyMetrics | None = None
    repetitions: int = 1
    continuous_stats: MetricsStats | None = None
    classification_stats: ClassificationStats | None = None
    failed: int = 0
    failed_items: list[str] = Field(default_factory=list)


class RegressionThresholds(BaseModel):
    """Пороги деградации для regression-гейта (ухудшение сильнее порога = fail)."""

    max_mae_reg: float = 0.3
    max_rmse_reg: float = 0.4
    max_acc_reg: float = 0.03
    min_spearman_reg: float = 0.02


class ReportComparison(BaseModel):
    """Сравнение текущего прогона с baseline: дельты метрик и флаг прохождения."""

    mae_delta: float
    rmse_delta: float
    accuracy_delta: float
    spearman_delta: float | None
    passed: bool


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
