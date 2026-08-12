"""Тесты схем выходных данных."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scoring_service.schemas import FitResult, JudgeResult, ReasoningSteps, ScoringOutput


def _reasoning() -> ReasoningSteps:
    return ReasoningSteps(
        procurement_essence="автоматизация",
        competencies_essence="разработка ПО",
        relevant_competencies="разработка",
        term_overlap_mismatch_check="нет",
        synonym_semantic_bridge="прямое",
        uncovered_scope="нет",
        tz_review_necessity="не нужно",
        fit_score_rationale="полное покрытие",
    )


def test_fit_result_clamps_score() -> None:
    fit = FitResult(reasoning=_reasoning(), fit_score=12.0, requires_tz_review=False)
    assert fit.fit_score == 10.0


def test_fit_result_negative_score() -> None:
    fit = FitResult(reasoning=_reasoning(), fit_score=-3.0, requires_tz_review=False)
    assert fit.fit_score == 0.0


def test_fit_result_keeps_tz_review_in_band() -> None:
    fit = FitResult(reasoning=_reasoning(), fit_score=5.0, requires_tz_review=True)
    assert fit.requires_tz_review is True


def test_fit_result_drops_tz_review_below_band() -> None:
    # Скор явно низкий (вне компетенций) — дорогое уточнение по ТЗ не запускаем.
    fit = FitResult(reasoning=_reasoning(), fit_score=3.0, requires_tz_review=True)
    assert fit.requires_tz_review is False
    assert fit.fit_score == 3.0


def test_fit_result_drops_tz_review_above_band() -> None:
    # Скор уже высокий (ясная автоматизация) — уточнение по ТЗ не нужно.
    fit = FitResult(reasoning=_reasoning(), fit_score=8.0, requires_tz_review=True)
    assert fit.requires_tz_review is False
    assert fit.fit_score == 8.0


def test_judge_result_validation() -> None:
    judge = JudgeResult(critics="ok", verdict="accept", final_fit_score=9.0)
    assert judge.verdict == "accept"


def test_judge_result_rejects_bad_verdict() -> None:
    with pytest.raises(ValidationError):
        JudgeResult(critics="x", verdict="unknown", final_fit_score=5.0)  # type: ignore[arg-type]


def test_scoring_output() -> None:
    out = ScoringOutput(
        procurement_id=1,
        description="desc",
        fit=FitResult(reasoning=_reasoning(), fit_score=8.0, requires_tz_review=True),
        judge=JudgeResult(critics="ok", verdict="accept", final_fit_score=8.0),
        final_fit_score=8.0,
        requires_tz_review=True,
        fit_multiplier=0.8,
        p_win=1.0,
        margin=100.0,
        score=800.0,
    )
    assert out.score == 800.0
    assert out.requires_tz_review is True
