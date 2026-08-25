"""Pydantic-модели метрик оценки качества Fit на тестовом наборе."""

from __future__ import annotations

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
