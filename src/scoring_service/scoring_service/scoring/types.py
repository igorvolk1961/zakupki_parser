"""Внутренние типы пайплайна скоринга."""

from __future__ import annotations

from dataclasses import dataclass

from scoring_service.schemas import FitResult, JudgeResult


@dataclass
class _PipelineResult:
    """Результат основного LLM-пайплайна (fit/judge/refine)."""

    description: str
    fit: FitResult
    judge: JudgeResult
    final_fit: float
    fit_norm: float
    requires_tz_review: bool
