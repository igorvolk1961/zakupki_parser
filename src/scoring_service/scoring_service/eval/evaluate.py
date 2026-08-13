"""Прогон fit-пайплайна по тестовому набору и расчёт метрик."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from scoring_service.eval.dataset import EvalItem, load_dataset, resolve_expected_verdict
from scoring_service.eval.metrics import (
    ClassificationMetrics,
    ClassificationStats,
    ConsistencyMetrics,
    EvaluationReport,
    Metrics,
    MetricsStats,
    RegressionThresholds,
    ReportComparison,
    classification_stats,
    compare_reports,
    compute_classification_metrics,
    compute_consistency,
    compute_metrics,
    mean_classification,
    mean_metrics,
    metrics_stats,
)
from scoring_service.llm_factory import build_llm, callbacks_for, langfuse_handler
from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.pipeline.judge_chain import JudgeChain
from scoring_service.scoring import Scorer
from scoring_service.settings import Settings


def _resolve_thresholds(
    max_mae: float, max_rmse: float, max_acc: float, min_spearman: float
) -> RegressionThresholds:
    return RegressionThresholds(
        max_mae_reg=max_mae,
        max_rmse_reg=max_rmse,
        max_acc_reg=max_acc,
        min_spearman_reg=min_spearman,
    )


def run_evaluation(
    settings: Settings,
    dataset: list[EvalItem],
    competencies: str | None = None,
    tolerance: float = 1.0,
    accept_threshold: float = 5.0,
    precision_k: int | None = None,
    repeat: int = 1,
) -> tuple[EvaluationReport, list[dict[str, Any]]]:
    """Прогнать пайплайн по датасету, вернуть полный отчёт и детальные результаты."""
    llm = build_llm(settings)
    handler = langfuse_handler(settings)
    callbacks = callbacks_for(handler)
    scorer = Scorer(
        FitChain(llm, callbacks),
        JudgeChain(llm, callbacks),
        settings,
        callbacks=callbacks,
    )
    comp = competencies or settings.competencies()

    expected = [item.expected_fit for item in dataset]
    expected_verdict = [resolve_expected_verdict(item, accept_threshold) for item in dataset]
    # [пример][повтор]: финальный скор, бизнес-решение, verdict судьи.
    predicted_all: list[list[float]] = []
    business_all: list[list[bool]] = []
    verdict_all: list[list[str]] = []
    details: list[dict[str, Any]] = []
    # Отдельный run_id на каждый пример: каждый — своя LangFuse-сессия/трейс,
    # чтобы в UI можно было разглядывать отдельную закупку (а не один общий трейс
    # на весь датасет). Повторы примера остаются в одном трейсе.
    for item in dataset:
        run_id = uuid.uuid4().hex
        record = {"subject": item.description}
        scores: list[float] = []
        business: list[bool] = []
        verdicts: list[str] = []
        first_result = None
        for _ in range(repeat):
            result = scorer.score(record, comp, run_id=run_id)
            if first_result is None:
                first_result = result
            scores.append(result.final_fit_score)
            # Бизнес-решение «брать/не брать» выводится порогом по финальному скору.
            # НЕ используем judge.verdict: это проверка адекватности оценки fit, а не
            # решение о целесообразности участия в закупке.
            business.append(result.final_fit_score >= accept_threshold)
            verdicts.append(result.judge.verdict)
        predicted_all.append(scores)
        business_all.append(business)
        verdict_all.append(verdicts)
        details.append(
            {
                "description": item.description,
                "expected_fit": item.expected_fit,
                "expected_verdict": expected_verdict[-1],
                "final_fit_score": scores[0],
                "verdict": verdicts[0],
                "critics": first_result.judge.critics if first_result else "",
            }
        )

    reps_metrics: list[Metrics] = []
    reps_class: list[ClassificationMetrics] = []
    for r in range(repeat):
        pred_r = [predicted_all[i][r] for i in range(len(dataset))]
        biz_r = [business_all[i][r] for i in range(len(dataset))]
        reps_metrics.append(compute_metrics(expected, pred_r, tolerance))
        reps_class.append(
            compute_classification_metrics(expected_verdict, biz_r, expected, pred_r, k=precision_k)
        )

    consistency: ConsistencyMetrics | None = None
    if repeat > 1:
        consistency = compute_consistency(predicted_all, verdict_all)

    if repeat == 1:
        continuous = reps_metrics[0]
        classification = reps_class[0]
        continuous_stats: MetricsStats | None = None
        class_stats: ClassificationStats | None = None
    else:
        continuous = mean_metrics(reps_metrics)
        classification = mean_classification(reps_class)
        continuous_stats = metrics_stats(reps_metrics)
        class_stats = classification_stats(reps_class)

    report = EvaluationReport(
        n=len(expected),
        continuous=continuous,
        classification=classification,
        consistency=consistency,
        repetitions=repeat,
        continuous_stats=continuous_stats,
        classification_stats=class_stats,
    )
    return report, details


def evaluate_cli(
    settings: Settings,
    dataset_path: Path,
    out_path: Path | None,
    competencies: str | None,
    tolerance: float,
    accept_threshold: float = 5.0,
    precision_k: int | None = None,
    repeat: int = 1,
    compare: Path | None = None,
    max_mae: float = 0.3,
    max_rmse: float = 0.4,
    max_acc: float = 0.03,
    min_spearman: float = 0.02,
) -> tuple[EvaluationReport, ReportComparison | None]:
    """Прогнать оценку, опционально записать отчёт и сравнить с baseline.

    Возвращает ``(report, comparison)``; ``comparison`` не None, если задан ``compare``.
    """
    dataset = load_dataset(dataset_path)
    report, details = run_evaluation(
        settings,
        dataset,
        competencies,
        tolerance,
        accept_threshold=accept_threshold,
        precision_k=precision_k,
        repeat=repeat,
    )
    if out_path is not None:
        out_path.write_text(
            json.dumps({"metrics": report.model_dump(), "items": details}, ensure_ascii=False),
            encoding="utf-8",
        )
    comparison: ReportComparison | None = None
    if compare is not None:
        baseline_raw = json.loads(compare.read_text(encoding="utf-8"))
        baseline = EvaluationReport.model_validate(baseline_raw["metrics"])
        thresholds = _resolve_thresholds(max_mae, max_rmse, max_acc, min_spearman)
        comparison = compare_reports(baseline.continuous, report.continuous, thresholds)
    return report, comparison


def _dump_report(report: EvaluationReport) -> str:
    return json.dumps(report.model_dump(), ensure_ascii=False, indent=2)


def _dump_comparison(comparison: ReportComparison) -> str:
    return json.dumps(comparison.model_dump(), ensure_ascii=False, indent=2)
