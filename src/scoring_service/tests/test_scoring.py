"""Тесты оркестратора скоринга (fit → judge → refine → Score)."""

from __future__ import annotations

from typing import Literal

from scoring_service.schemas import FitResult, JudgeResult, ReasoningSteps
from scoring_service.scoring import Scorer, build_scorer
from scoring_service.settings import Settings


def _reasoning() -> ReasoningSteps:
    return ReasoningSteps(
        procurement_essence="s",
        competencies_essence="s",
        relevant_competencies="s",
        term_overlap_mismatch_check="s",
        synonym_semantic_bridge="s",
        uncovered_scope="s",
        fit_score_rationale="s",
    )


def _fit(score: float) -> FitResult:
    return FitResult(reasoning=_reasoning(), fit_score=score)


def _judge(verdict: Literal["accept", "reject"], final: float, critics: str = "") -> JudgeResult:
    return JudgeResult(critics=critics, verdict=verdict, final_fit_score=final)


class _FakeFit:
    def __init__(self, scores: list[float]) -> None:
        self._scores = list(scores)
        self.calls = 0

    def invoke(
        self,
        competencies: str,
        description: str,
        procurement_id: str | None = None,
    ) -> FitResult:
        self.calls += 1
        value = self._scores.pop(0) if self._scores else 5.0
        return _fit(value)


class _FakeJudge:
    def __init__(self, verdicts: list[JudgeResult]) -> None:
        self._verdicts = list(verdicts)
        self.calls = 0

    def invoke(
        self,
        competencies: str,
        description: str,
        fit_result: FitResult,
        procurement_id: str | None = None,
    ) -> JudgeResult:
        self.calls += 1
        return self._verdicts.pop(0)


def _scorer(fit: _FakeFit, judge: _FakeJudge) -> Scorer:
    return Scorer(fit, judge, Settings(p_win=1.0, margin_rate=1.0, score_use_stub=False))  # type: ignore[arg-type]


def test_score_accept_path() -> None:
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    out = _scorer(fit, judge).score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")
    assert out.final_fit_score == 8.0
    assert out.score == 80.0  # fit 8/10 × p_win 1.0 × margin 100
    assert out.p_win == 1.0
    assert out.margin == 100.0
    assert fit.calls == 1
    assert judge.calls == 1


def test_score_normalizes_fit_by_10() -> None:
    """Fit из модели (0–10) делится на 10 перед умножением на p_win и margin."""
    fit = _FakeFit([6.0])
    judge = _FakeJudge([_judge("accept", 6.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(p_win=0.5, margin_rate=1.0, score_use_stub=False),
    )  # type: ignore[arg-type]
    out = scorer.score({"subject": "x", "nmck": 400.0}, "comp")
    # fit_norm = 6/10 = 0.6; score = 0.6 × 0.5 × 400 = 120.0
    assert out.final_fit_score == 6.0
    assert out.p_win == 0.5
    assert out.margin == 400.0
    assert out.score == 120.0


def test_score_reject_refines_once() -> None:
    fit = _FakeFit([6.0, 7.0])
    judge = _FakeJudge([_judge("reject", 6.0, critics="завышено"), _judge("accept", 7.0)])
    out = _scorer(fit, judge).score({"subject": "x", "nmck": 50.0}, "comp")
    assert out.final_fit_score == 7.0
    assert out.score == 35.0
    assert fit.calls == 2
    assert judge.calls == 2


def test_score_zero_nmck() -> None:
    fit = _FakeFit([9.0])
    judge = _FakeJudge([_judge("accept", 9.0)])
    out = _scorer(fit, judge).score({"subject": "x", "nmck": 0}, "comp")
    assert out.score == 0.0


def test_score_no_normalization_uses_raw_fit() -> None:
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(p_win=1.0, margin_rate=1.0, normalize_fit_for_score=False, score_use_stub=False),
    )
    out = scorer.score({"subject": "x", "nmck": 100.0}, "comp")
    assert out.score == 800.0


def test_score_keeps_judge_final_over_fit() -> None:
    fit = _FakeFit([3.0, 3.0])
    judge = _FakeJudge([_judge("reject", 9.0, critics="занижено"), _judge("accept", 9.0)])
    out = _scorer(fit, judge).score({"subject": "x", "nmck": 10.0}, "comp")
    assert out.final_fit_score == 9.0
    assert out.score == 9.0
    assert fit.calls == 2


def test_stub_returns_existing_score_without_chains() -> None:
    scorer = Scorer(None, None, Settings(score_use_stub=True, p_win=1.0, margin_rate=1.0))
    out = scorer.score({"subject": "x", "nmck": 5000.0, "score": 123.45}, "comp")
    assert out.score == 123.45
    assert out.judge.verdict == "accept"
    assert out.margin == 5000.0


def test_stub_defaults_zero_score_when_missing() -> None:
    scorer = Scorer(None, None, Settings(score_use_stub=True))
    out = scorer.score({"subject": "x"}, "comp")
    assert out.score == 0.0


def test_build_scorer_stub_does_not_build_llm() -> None:
    scorer = build_scorer(Settings(score_use_stub=True))
    assert scorer._fit is None  # noqa: SLF001
    assert scorer._judge is None  # noqa: SLF001
