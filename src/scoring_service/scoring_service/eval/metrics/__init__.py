"""Метрики качества оценки Fit на тестовом наборе.

Непрерывные метрики (``compute_metrics``) сравнивают предсказанные и ожидаемые
скоры; классификационные (``compute_classification_metrics``) — решения судьи
(accept/reject) с бинарной меткой; ``compute_consistency`` оценивает стабильность
пайплайна при повторных прогонах; ``compare_reports`` — regression-гейт при деплое.

Реализация разбита на подпакеты: ``models`` (Pydantic-схемы), ``regression``
(непрерывные метрики и усреднение), ``classification`` (бинарные метрики).
Здесь — реэкспорт для совместимости с прежним модулем ``eval/metrics.py``.
"""

from __future__ import annotations

from scoring_service.eval.metrics.classification import (
    classification_stats,
    compute_classification_metrics,
    mean_classification,
)
from scoring_service.eval.metrics.models import (
    ClassificationMetrics,
    ClassificationStats,
    ConsistencyMetrics,
    EvaluationReport,
    Metrics,
    MetricsStats,
    RegressionThresholds,
    ReportComparison,
)
from scoring_service.eval.metrics.regression import (
    compare_reports,
    compute_consistency,
    compute_metrics,
    mean_metrics,
    metrics_stats,
)

__all__ = [
    "ClassificationMetrics",
    "ClassificationStats",
    "ConsistencyMetrics",
    "EvaluationReport",
    "Metrics",
    "MetricsStats",
    "RegressionThresholds",
    "ReportComparison",
    "classification_stats",
    "compare_reports",
    "compute_classification_metrics",
    "compute_consistency",
    "compute_metrics",
    "mean_classification",
    "mean_metrics",
    "metrics_stats",
]
