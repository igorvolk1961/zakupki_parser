"""Прогон fit-пайплайна по тестовому набору и расчёт метрик."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from scoring_service.eval.dataset import EvalItem, load_dataset
from scoring_service.eval.metrics import Metrics, compute_metrics
from scoring_service.llm_factory import build_llm, callbacks_for, langfuse_handler
from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.pipeline.judge_chain import JudgeChain
from scoring_service.scoring import Scorer
from scoring_service.settings import Settings


def run_evaluation(
    settings: Settings,
    dataset: list[EvalItem],
    competencies: str | None = None,
    tolerance: float = 1.0,
) -> tuple[Metrics, list[dict[str, Any]]]:
    """Прогнать пайплайн по датасету, вернуть метрики и детальные результаты."""
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
    details: list[dict[str, Any]] = []
    # Один run_id на весь датасет: все примеры — в одной LangFuse-сессии.
    run_id = uuid.uuid4().hex
    for item in dataset:
        record = {"subject": item.description}
        result = scorer.score(record, comp, run_id=run_id)
        expected.append(item.expected_fit)
        predicted.append(result.final_fit_score)
        details.append(
            {
                "description": item.description,
                "expected_fit": item.expected_fit,
                "final_fit_score": result.final_fit_score,
                "verdict": result.judge.verdict,
                "critics": result.judge.critics,
            }
        )

    metrics = compute_metrics(expected, predicted, tolerance)
    return metrics, details


def evaluate_cli(
    settings: Settings,
    dataset_path: Path,
    out_path: Path | None,
    competencies: str | None,
    tolerance: float,
) -> Metrics:
    dataset = load_dataset(dataset_path)
    metrics, details = run_evaluation(settings, dataset, competencies, tolerance)
    if out_path is not None:
        out_path.write_text(
            json.dumps({"metrics": metrics.model_dump(), "items": details}, ensure_ascii=False),
            encoding="utf-8",
        )
    return metrics
