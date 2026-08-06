"""Pydantic-схемы выхода LLM-цепочек скоринга.

``FitResult`` — результат fit-цепочки (reasoning + fit_score 0..10).
``JudgeResult`` — результат судьи (critics / verdict / final_fit_score).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ReasoningSteps(BaseModel):
    """Обязательные этапы рассуждений fit-цепочки."""

    procurement_essence: str = Field(description="Суть закупки (кратко, из описания)")
    competencies_essence: str = Field(description="Суть компетенций поставщика")
    relevant_competencies: str = Field(description="Какие компетенции релевантны закупке")
    term_overlap_mismatch_check: str = Field(
        description="Проверка: термины совпадают, но смысл разный (false-friend)"
    )
    synonym_semantic_bridge: str = Field(
        description="Релевантность через синонимичные термины при другой лексике"
    )
    uncovered_scope: str = Field(description="Что из закупки НЕ покрывается компетенциями")
    fit_score_rationale: str = Field(description="Обоснование числовой оценки fit_score")


class FitResult(BaseModel):
    """Результат fit-цепочки: рассуждения + числовая оценка 0..10."""

    reasoning: ReasoningSteps
    fit_score: float = Field(description="Оценка Fit от 0 до 10")

    @field_validator("fit_score")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(10.0, round(value, 2)))


class JudgeResult(BaseModel):
    """Результат судьи: оценка адекватности fit-оценки."""

    critics: str = Field(description="Критические замечания либо согласие с оценкой")
    verdict: Literal["accept", "reject"]
    final_fit_score: float = Field(description="Финальная оценка Fit")

    @field_validator("final_fit_score")
    @classmethod
    def _clamp_final(cls, value: float) -> float:
        return max(0.0, min(10.0, round(value, 2)))


class ScoringOutput(BaseModel):
    """Полный результат скоринга одной закупки."""

    procurement_id: int | None = None
    description: str
    fit: FitResult
    judge: JudgeResult
    final_fit_score: float = Field(description="Финальная оценка Fit 0..10")
    p_win: float
    margin: float
    score: float

    @field_validator("final_fit_score")
    @classmethod
    def _clamp_final_out(cls, value: float) -> float:
        return max(0.0, min(10.0, round(value, 2)))
