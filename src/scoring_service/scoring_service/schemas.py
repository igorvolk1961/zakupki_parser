"""Pydantic-схемы выхода LLM-цепочек скоринга.

``FitResult`` — результат fit-цепочки (reasoning + fit_score 0..10).
``JudgeResult`` — результат судьи (critics / verdict / final_fit_score).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    tz_review_necessity: str = Field(
        description=(
            "Нужно ли уточнять скор по тексту ТЗ (requires_tz_review) и почему: "
            "обоснуй, есть ли реальная неоднозначность (например, не названо ПО), и почему "
            "уточнение оправдано (закупка иначе потенциально релевантна). Уточнение дорогое — "
            "не запускай его, если закупка уже однозначно вне/внутри компетенций. Если уточнение "
            "нужно, укажи, достаточно ли прочитать только полный заголовок ТЗ (тогда "
            "requires_tz_body=false) или необходимо читать всё тело ТЗ (requires_tz_body=true), "
            "и почему."
        )
    )
    fit_score_rationale: str = Field(description="Обоснование числовой оценки fit_score")


class FitResult(BaseModel):
    """Результат fit-цепочки: рассуждения + числовая оценка 0..10."""

    reasoning: ReasoningSteps
    fit_score: float = Field(description="Оценка Fit от 0 до 10")
    requires_tz_review: bool = Field(
        description=(
            "True, если по краткому/обрезанному описанию закупки невозможно однозначно "
            "установить, идёт ли речь о сопровождении чужого ПО или об автоматизации "
            "бизнес-процессов (например, используемое ПО не названо или описание обрезано "
            "многоточием). Тогда скор нужно уточнить по тексту ТЗ."
        )
    )
    requires_tz_body: bool = Field(
        default=True,
        description=(
            "Нужно ли для уточнения скора читать всё тело ТЗ (requires_tz_body=true), или "
            "достаточно только полного заголовка ТЗ (requires_tz_body=false). Имеет смысл, "
            "когда описание закупки обрезано многоточием. Действует только при "
            "requires_tz_review=true: requires_tz_body=false означает чтение только заголовка ТЗ."
        ),
    )

    @field_validator("fit_score")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(10.0, round(value, 2)))

    @model_validator(mode="after")
    def _guard_tz_review_band(self) -> FitResult:
        """Не запускать дорогое уточнение по ТЗ вне плаузибельной зоны скор 4..7.

        requires_tz_review=true оправдан только при скор в среднем диапазоне:
        иначе (явно вне компетенций либо уже ясная автоматизация) уточнение
        по полному тексту ТЗ не изменит итог. Флаг безопасно сбрасывается.
        """
        if self.requires_tz_review and not 4.0 <= self.fit_score <= 7.0:
            self.requires_tz_review = False
        # requires_tz_body=false (только заголовок) допустим лишь при requires_tz_review=true.
        if self.requires_tz_body is False and not self.requires_tz_review:
            self.requires_tz_body = True
        return self


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
    requires_tz_review: bool = Field(
        description="Нужно ли уточнить скор по тексту ТЗ (из fit.requires_tz_review)"
    )
    requires_tz_body: bool = Field(
        default=True,
        description=(
            "Нужно ли читать всё тело ТЗ (из fit.requires_tz_body); false = только заголовок"
        ),
    )
    fit_multiplier: float = Field(
        description=(
            "Множитель Fit 0..1 (final_fit_score / max_fit_score при normalize_fit_for_score)"
        )
    )
    p_win: float
    margin: float
    score: float
    # Ветка векторной близости (Giga Embedder). None, если ветка выключена/не
    # настроен ключ доступа/произошёл сбой (best-effort).
    embedding_similarity: float | None = None

    @field_validator("final_fit_score")
    @classmethod
    def _clamp_final_out(cls, value: float) -> float:
        return max(0.0, min(10.0, round(value, 2)))
