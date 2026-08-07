"""Оркестратор пайплайна скоринга.

Поток:
1. извлечь описание закупки из карточки (``pipeline.description``);
2. fit-цепочка: reasoning + fit_score (0..10);
3. judge-цепочка: critics / verdict / final_fit_score;
4. если verdict == reject — до ``num_refine_rounds`` повторный fit с учётом critics,
   затем повторный judge;
5. Score = final_fit_score × P(win) × Margin.

Если включён флаг ``score_use_stub`` — LLM-пайплайн не запускается: возвращается
score, уже присутствующий в данных закупки (см. ``build_scorer``).
"""

from __future__ import annotations

from typing import Any

from scoring_service.llm_factory import build_llm, callbacks_for, langfuse_handler
from scoring_service.modules import margin as margin_module
from scoring_service.modules import p_win as p_win_module
from scoring_service.pipeline.description import extract_description
from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.pipeline.judge_chain import JudgeChain
from scoring_service.schemas import FitResult, JudgeResult, ReasoningSteps, ScoringOutput
from scoring_service.settings import Settings


class Scorer:
    """Оркестратор: карточка + компетенции → полный результат скоринга."""

    def __init__(
        self,
        fit_chain: FitChain | None,
        judge_chain: JudgeChain | None,
        settings: Settings,
    ) -> None:
        self._fit = fit_chain
        self._judge = judge_chain
        self._settings = settings

    def _refine_fit(
        self,
        competencies: str,
        description: str,
        critics: str,
        procurement_id: str | None,
    ) -> FitResult:
        """Повторный fit с учётом критики судьи (best-effort: без гарантии JSON)."""
        messages_hint = (
            f"Судья дал замечания: {critics}\nПересмотри оценку с учётом этих замечаний."
        )
        return self._fit.invoke(  # type: ignore[union-attr]
            f"{competencies}\n\nДополнительно: {messages_hint}",
            description,
            procurement_id,
        )

    def _stub_score(self, record: dict[str, Any], procurement_id: int | None) -> ScoringOutput:
        """Заглушка: возвращает score, уже имеющийся в данных закупки (без LLM).

        Fit/Judge — пустые плейсхолдеры; score берётся из карточки ``record["score"]``.
        """
        existing = float(record.get("score") or 0.0)
        score = round(existing, self._settings.score_round_digits)
        description = extract_description(record)
        reasoning = ReasoningSteps(
            procurement_essence="",
            competencies_essence="",
            relevant_competencies="",
            term_overlap_mismatch_check="",
            synonym_semantic_bridge="",
            uncovered_scope="",
            fit_score_rationale="stub",
        )
        fit = FitResult(reasoning=reasoning, fit_score=score)
        judge = JudgeResult(
            critics="Stub: возвращён существующий score закупки",
            verdict="accept",
            final_fit_score=score,
        )
        return ScoringOutput(
            procurement_id=procurement_id,
            description=description,
            fit=fit,
            judge=judge,
            final_fit_score=score,
            p_win=p_win_module.p_win(record, self._settings),
            margin=margin_module.margin(record, self._settings),
            score=score,
        )

    def score(
        self,
        record: dict[str, Any],
        competencies: str,
        procurement_id: int | None = None,
    ) -> ScoringOutput:
        """Полный скоринг закупки по карточке и компетенциям."""
        if self._settings.score_use_stub:
            return self._stub_score(record, procurement_id)

        description = extract_description(record)
        session_id = str(procurement_id) if procurement_id is not None else None

        fit = self._fit.invoke(competencies, description, session_id)  # type: ignore[union-attr]
        judge = self._judge.invoke(  # type: ignore[union-attr]
            competencies, description, fit, session_id
        )

        for _ in range(self._settings.num_refine_rounds):
            if judge.verdict != "reject":
                break
            fit = self._refine_fit(competencies, description, judge.critics, session_id)
            judge = self._judge.invoke(  # type: ignore[union-attr]
                competencies, description, fit, session_id
            )

        final_fit = judge.final_fit_score
        pwin = p_win_module.p_win(record, self._settings)
        marg = margin_module.margin(record, self._settings)
        # Приводим Fit (0-10) к шкале парсера (0-1), чтобы Score не был в ~10 раз больше
        # дефолтного. Выключается флагом normalize_fit_for_score.
        fit_norm = (
            final_fit / self._settings.max_fit_score
            if self._settings.normalize_fit_for_score
            else final_fit
        )
        score = round(fit_norm * pwin * marg, self._settings.score_round_digits)

        return ScoringOutput(
            procurement_id=procurement_id,
            description=description,
            fit=fit,
            judge=judge,
            final_fit_score=final_fit,
            p_win=pwin,
            margin=marg,
            score=score,
        )


def build_scorer(settings: Settings) -> Scorer:
    """Построить ``Scorer``: заглушку (``score_use_stub``) либо LLM-пайплайн.

    В режиме заглушки LLM/LangChain-цепочки не создаются, поэтому сервис работает
    без ключа/провайдера, пока пайплайн не отлажен.
    """
    if settings.score_use_stub:
        return Scorer(None, None, settings)
    llm = build_llm(settings)
    callbacks = callbacks_for(langfuse_handler(settings))
    return Scorer(
        FitChain(llm, callbacks),
        JudgeChain(llm, callbacks),
        settings,
    )
