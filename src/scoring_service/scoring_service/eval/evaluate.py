"""Прогон fit-пайплайна по тестовому набору и расчёт метрик."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from scoring_service.eval.dataset import EvalItem, load_dataset, resolve_expected_verdict
from scoring_service.eval.metrics import (
    ConsistencyMetrics,
    EvaluationReport,
    RegressionThresholds,
    ReportComparison,
    compare_reports,
    compute_classification_metrics,
    compute_consistency,
    compute_metrics,
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
    )
    comp = competencies or settings.competencies()

    expected: list[float] = []
    predicted: list[float] = []
    expected_verdict: list[bool] = []
    predicted_verdict: list[bool] = []
    details: list[dict[str, Any]] = []
    repeated_scores: list[list[float]] = []
    repeated_verdicts: list[list[str]] = []
    # Один run_id на весь датасет: все примеры — в одной LangFuse-сессии.
    run_id = uuid.uuid4().hex
    for item in dataset:
        record = {"subject": item.description}
        result = scorer.score(record, comp, run_id=run_id)
        expected.append(item.expected_fit)
        predicted.append(result.final_fit_score)
        expected_verdict.append(resolve_expected_verdict(item, accept_threshold))
        # Бизнес-решение «брать/не брать» выводится порогом по финальному скору.
        # НЕ используем judge.verdict: это проверка адекватности оценки fit, а не
        # решение о целесообразности участия в закупке.
        predicted_verdict.append(result.final_fit_score >= accept_threshold)
        details.append(
            {
                "description": item.description,
                "expected_fit": item.expected_fit,
                "expected_verdict": resolve_expected_verdict(item, accept_threshold),
                "final_fit_score": result.final_fit_score,
                "verdict": result.judge.verdict,
                "critics": result.judge.critics,
            }
        )
        if repeat > 1:
            scores: list[float] = []
            verdicts: list[str] = []
            for _ in range(repeat):
                r = scorer.score(record, comp, run_id=run_id)
                scores.append(r.final_fit_score)
                verdicts.append(r.judge.verdict)
            repeated_scores.append(scores)
            repeated_verdicts.append(verdicts)

    continuous = compute_metrics(expected, predicted, tolerance)
    classification = compute_classification_metrics(
        expected_verdict,
        predicted_verdict,
        expected,
        predicted,
        k=precision_k,
    )
    consistency: ConsistencyMetrics | None = None
    if repeat > 1:
        consistency = compute_consistency(repeated_scores, repeated_verdicts)

    report = EvaluationReport(
        n=len(expected),
        continuous=continuous,
        classification=classification,
        consistency=consistency,
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
