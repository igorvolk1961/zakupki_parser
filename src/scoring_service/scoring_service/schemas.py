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
    uncovered_scope: str = Field(
        description=(
            "Что из закупки НЕ покрывается компетенциями (различай слабый сигнал — предмет "
            "не перечислен в профиле, но входит в его семантическое поле — и сильный — "
            "закупка вне компетенций либо в исключениях профиля)"
        )
    )
    tz_review_necessity: str = Field(
        description=(
            "Нужно ли уточнять скор по тексту ТЗ (requires_tz_review) и почему: "
            "обоснуй, есть ли реальная неоднозначность (например, не названо ПО), и почему "
            "уточнение оправдано (закупка иначе потенциально релевантна). Уточнение дорогое — "
            "не запускай его, если закупка уже однозначно вне/внутри компетенций."
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
            "установить, входит ли она в компетенции профиля (например, не названо конкретное "
            "ПО и неясно, идёт ли речь о сопровождении чужого готового продукта или о работе "
            "в рамках компетенций) либо описание обрезано многоточием. Тогда скор нужно "
            "уточнить по тексту ТЗ."
        )
    )

    @field_validator("fit_score")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(10.0, round(value, 2)))

    @model_validator(mode="after")
    def _guard_tz_review_band(self) -> FitResult:
        """Не запускать дорогое уточнение по ТЗ вне зоны потенциальной релевантности скор 4..7.

        requires_tz_review=true оправдан только при скор в среднем диапазоне:
        иначе (явно вне компетенций либо уже ясная автоматизация) уточнение
        по полному тексту ТЗ не изменит итог. Флаг безопасно сбрасывается.
        """
        if self.requires_tz_review and not 4.0 <= self.fit_score <= 7.0:
            self.requires_tz_review = False
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
    fit_multiplier: float = Field(
        description=(
            "Множитель Fit 0..1 (final_fit_score / max_fit_score при normalize_fit_for_score)"
        )
    )
    score: float
    # Способ скоринга для результата (пишется в БД парсера): "fit" — обычный
    # LLM-пайплайн; "sim" — предварительная фильтрация по векторной близости
    # (LLM не выполнялся, fit_score=0).
    score_method: str = "fit"
    # Ветка векторной близости (Giga Embedder). None, если ветка выключена/не
    # настроен ключ доступа/произошёл сбой (best-effort).
    embedding_similarity: float | None = None
    # URL трейса LangFuse для этой закупки (глубокоя ссылка на трейс в UI).
    # None, если LangFuse не настроен/недоступен либо трейс не создан — тогда
    # кнопка «Трейс» на карточке не отображается. Ссылка строится на стороне
    # скоринга по trace_id, созданному мониторингом root-run одной закупки.
    langfuse_trace_url: str | None = None
    # Стоимость LLM-вызовов скоринга (fit/judge/refine) в USD. Заполняется
    # CostCallback-колбэком; None, если сбор стоимости не подключён.
    cost_usd: float | None = None

    @field_validator("final_fit_score")
    @classmethod
    def _clamp_final_out(cls, value: float) -> float:
        return max(0.0, min(10.0, round(value, 2)))
