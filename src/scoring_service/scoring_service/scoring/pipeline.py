"""Основной LLM-пайплайн (fit → tz_review → judge → refine) и сборка результата.

Миксин, используемый классом ``Scorer``. Уточнение по тексту ТЗ (``tz_review``)
восстанавливает обрезанное описание; глубокий анализ тела ТЗ (стоп-условия)
вынесен в analysis_service (on-demand).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from scoring_service.pipeline.description import (
    extend_description_from_tz,
    is_truncated_description,
)
from scoring_service.pipeline.tz_reviewer import TzReviewOutcome
from scoring_service.schemas import FitResult, ScoringOutput
from scoring_service.scoring.types import _PipelineResult

logger = logging.getLogger(__name__)


class PipelineMixin:
    """Fit/judge/refine-пайплайн и построение финального результата."""

    def _refine_fit(
        self,
        competencies: str,
        description: str,
        critics: str,
        session_id: str | None,
        metadata: dict[str, Any],
        parent_config: RunnableConfig,
    ) -> FitResult:
        """Повторный fit с учётом критики судьи (best-effort: без гарантии JSON)."""
        messages_hint = (
            f"Судья дал замечания: {critics}\nПересмотри оценку с учётом этих замечаний."
        )
        return self._fit.invoke(  # type: ignore[union-attr]
            f"{competencies}\n\nДополнительно: {messages_hint}",
            description,
            session_id,
            metadata,
            parent_config=parent_config,
            run_name="fit_refine",
        )

    def _run_pipeline(
        self,
        record: dict[str, Any],
        competencies: str,
        description: str,
        session_id: str | None,
        trace_meta: dict[str, Any],
        parent_config: RunnableConfig,
    ) -> _PipelineResult:
        """Основной LLM-пайплайн (fit → tz_review → judge → refine)."""
        # Описание обрезано многоточием: явно сообщаем модели о неполноте описания.
        truncated = is_truncated_description(description)

        fit = self._fit.invoke(  # type: ignore[union-attr]
            competencies,
            description,
            session_id,
            trace_meta,
            parent_config=parent_config,
            truncated=truncated,
        )

        # Уточнение по тексту ТЗ: если fit запросил (requires_tz_review), ищем файл ТЗ
        # в карточке и извлекаем его текст. Стадия Fit обрабатывает ТОЛЬКО описание
        # закупки (восстановление обрезанного описания из текста ТЗ) — глубокий
        # анализ тела ТЗ (стоп-условия) вынесен в analysis_service (on-demand).
        tz_outcome: TzReviewOutcome | None = None
        effective_description = description
        # True, только если уточнение реально состоялось (ТЗ найден и текст непустой).
        tz_refined = False
        if fit.requires_tz_review and self._tz_reviewer is not None:
            tz_outcome = self._tz_reviewer.invoke(record, parent_config, trace_meta, session_id)
            if tz_outcome.found and tz_outcome.description and tz_outcome.description.strip():
                tz_refined = True
                extended = extend_description_from_tz(description, tz_outcome.description)
                if extended:
                    effective_description = extended
                    fit = self._fit.invoke(  # type: ignore[union-attr]
                        competencies,
                        effective_description,
                        session_id,
                        trace_meta,
                        parent_config=parent_config,
                        run_name="fit_tz",
                    )

        judge = self._judge.invoke(  # type: ignore[union-attr]
            competencies,
            effective_description,
            fit,
            session_id,
            trace_meta,
            parent_config=parent_config,
        )

        for _ in range(self._settings.num_refine_rounds):
            if judge.verdict != "reject":
                break
            fit = self._refine_fit(
                competencies,
                effective_description,
                judge.critics,
                session_id,
                trace_meta,
                parent_config,
            )
            judge = self._judge.invoke(  # type: ignore[union-attr]
                competencies,
                effective_description,
                fit,
                session_id,
                trace_meta,
                parent_config=parent_config,
            )

        final_fit = judge.final_fit_score
        # Приводим Fit (0-10) к шкале парсера (0-1), чтобы Score не был в ~10 раз больше
        # дефолтного. Выключается флагом normalize_fit_for_score.
        fit_norm = (
            final_fit / self._settings.max_fit_score
            if self._settings.normalize_fit_for_score
            else final_fit
        )

        return _PipelineResult(
            description=effective_description,
            fit=fit,
            judge=judge,
            final_fit=final_fit,
            fit_norm=fit_norm,
            # Флаг остаётся, если уточнение не запрошено или не состоялось
            # (ТЗ не найден / текст пуст) — скор не уточнён. При успешном
            # уточнении снимаем флаг (глубокое чтение тела ТЗ не выполняется).
            requires_tz_review=(
                fit.requires_tz_review if tz_outcome is None or not tz_refined else False
            ),
            requires_tz_body=False,
        )

    def _build_output(
        self,
        result: _PipelineResult,
        embed_sim: float | None,
        procurement_id: int | None,
    ) -> ScoringOutput:
        """Финальный ScoringOutput с учётом ветки векторной близости."""
        # Смешиваем Fit с веткой векторной близости, если ветка выполнена и alpha>0.
        base = result.fit_norm
        if embed_sim is not None and self._settings.giga_embedding_alpha > 0:
            alpha = self._settings.giga_embedding_alpha
            base = (1 - alpha) * base + alpha * embed_sim
        score = round(base, self._settings.score_round_digits)

        return ScoringOutput(
            procurement_id=procurement_id,
            description=result.description,
            fit=result.fit,
            judge=result.judge,
            final_fit_score=result.final_fit,
            requires_tz_review=result.requires_tz_review,
            requires_tz_body=result.requires_tz_body,
            fit_multiplier=result.fit_norm,
            score=score,
            embedding_similarity=embed_sim,
        )
