"""Тесты оркестратора скоринга (fit → judge → refine → Score)."""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.schemas import FitResult, JudgeResult, ReasoningSteps
from scoring_service.scoring import Scorer, build_scorer
from scoring_service.settings import Settings


class _FakeStructuredLLM(BaseChatModel):
    """Минимальный LLM: только для сборки FitChain (без сети)."""

    def with_structured_output(self, schema: object, **kwargs: object) -> RunnableLambda:
        return RunnableLambda(lambda messages, **kw: _fit(8.0))  # type: ignore[arg-type]

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: object,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="{}"))])

    @property
    def _llm_type(self) -> str:
        return "fake"


def _reasoning() -> ReasoningSteps:
    return ReasoningSteps(
        procurement_essence="s",
        competencies_essence="s",
        relevant_competencies="s",
        term_overlap_mismatch_check="s",
        synonym_semantic_bridge="s",
        uncovered_scope="s",
        tz_review_necessity="s",
        fit_score_rationale="s",
    )


def _fit(score: float, requires_tz_review: bool = False) -> FitResult:
    return FitResult(reasoning=_reasoning(), fit_score=score, requires_tz_review=requires_tz_review)


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
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
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
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
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
    assert out.fit_multiplier == 0.6
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


def test_score_propagates_requires_tz_review() -> None:
    class _FlaggedFit:
        def invoke(
            self,
            competencies: str,
            description: str,
            session_id: str | None = None,
            metadata: dict[str, object] | None = None,
        ) -> FitResult:
            return _fit(5.0, requires_tz_review=True)

    judge = _FakeJudge([_judge("accept", 5.0)])
    scorer = Scorer(_FlaggedFit(), judge, Settings(score_use_stub=False))  # type: ignore[arg-type]
    out = scorer.score({"subject": "x", "nmck": 10.0}, "comp")
    assert out.requires_tz_review is True


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


def test_score_passes_run_id_as_session_and_hyperparams_in_metadata() -> None:
    class _RecordingFit:
        def __init__(self, score: float) -> None:
            self._score = score
            self.calls: list[tuple[str | None, dict[str, object] | None]] = []

        def invoke(
            self,
            competencies: str,
            description: str,
            session_id: str | None = None,
            metadata: dict[str, object] | None = None,
        ) -> FitResult:
            self.calls.append((session_id, metadata))
            return _fit(self._score)

    fit = _RecordingFit(8.0)
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit, judge, Settings(p_win=0.5, margin_rate=0.2, llm_model="gpt-test", score_use_stub=False)
    )  # type: ignore[arg-type]
    scorer.score({"subject": "x", "nmck": 10.0}, "comp", procurement_id=42, run_id="run-1")

    assert fit.calls
    session_id, metadata = fit.calls[0]
    assert session_id == "run-1"
    assert metadata is not None
    assert metadata["procurement_id"] == 42
    assert metadata["run_id"] == "run-1"
    assert metadata["llm_model"] == "gpt-test"
    assert metadata["p_win"] == 0.5


def test_score_falls_back_to_procurement_session_without_run_id() -> None:
    class _RecordingFit:
        def __init__(self) -> None:
            self.session_ids: list[str | None] = []

        def invoke(
            self,
            competencies: str,
            description: str,
            session_id: str | None = None,
            metadata: dict[str, object] | None = None,
        ) -> FitResult:
            self.session_ids.append(session_id)
            return _fit(5.0)

    fit = _RecordingFit()
    judge = _FakeJudge([_judge("accept", 5.0)])
    scorer = Scorer(fit, judge, Settings(score_use_stub=False))  # type: ignore[arg-type]
    scorer.score({"subject": "x", "nmck": 10.0}, "comp", procurement_id=7)

    assert fit.session_ids == ["7"]


def test_fit_chain_config_sets_session_via_langfuse_session_id() -> None:
    """langfuse 4.x читает session_id из metadata['langfuse_session_id']."""
    chain = FitChain(_FakeStructuredLLM(), callbacks=None)
    cfg = chain._config(  # noqa: SLF001
        session_id="run-1", metadata={"procurement_id": 42, "llm_model": "gpt-test"}
    )
    meta = cfg.get("metadata", {})
    assert meta["langfuse_session_id"] == "run-1"
    assert meta["procurement_id"] == 42
    assert meta["llm_model"] == "gpt-test"

    cfg_no_session = chain._config(metadata={"procurement_id": 7})  # noqa: SLF001
    assert "langfuse_session_id" not in cfg_no_session.get("metadata", {})
