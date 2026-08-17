"""Тесты оркестратора скоринга (fit → judge → refine → Score)."""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.pipeline.tz_reviewer import TzReviewOutcome
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


def _fit(
    score: float,
    requires_tz_review: bool = False,
    requires_tz_body: bool = True,
) -> FitResult:
    return FitResult(
        reasoning=_reasoning(),
        fit_score=score,
        requires_tz_review=requires_tz_review,
        requires_tz_body=requires_tz_body,
    )


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
        parent_config: object | None = None,
        run_name: str = "fit_scoring",
        truncated: bool = False,
        full_text: bool = False,
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
        parent_config: object | None = None,
    ) -> JudgeResult:
        self.calls += 1
        return self._verdicts.pop(0)


def _scorer(fit: _FakeFit, judge: _FakeJudge) -> Scorer:
    return Scorer(fit, judge, Settings(score_use_stub=False))  # type: ignore[arg-type]


def test_score_accept_path() -> None:
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    out = _scorer(fit, judge).score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")
    assert out.final_fit_score == 8.0
    assert out.score == 0.8  # fit 8/10, стадия Fit возвращает только множитель
    assert out.fit_multiplier == 0.8
    assert fit.calls == 1
    assert judge.calls == 1


def test_score_normalizes_fit_by_10() -> None:
    """Fit из модели (0–10) делится на 10 — score = множитель Fit."""
    fit = _FakeFit([6.0])
    judge = _FakeJudge([_judge("accept", 6.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False),
    )  # type: ignore[arg-type]
    out = scorer.score({"subject": "x", "nmck": 400.0}, "comp")
    # fit_norm = 6/10 = 0.6; score = 0.6
    assert out.final_fit_score == 6.0
    assert out.fit_multiplier == 0.6
    assert out.score == 0.6


def test_score_reject_refines_once() -> None:
    fit = _FakeFit([6.0, 7.0])
    judge = _FakeJudge([_judge("reject", 6.0, critics="завышено"), _judge("accept", 7.0)])
    out = _scorer(fit, judge).score({"subject": "x", "nmck": 50.0}, "comp")
    assert out.final_fit_score == 7.0
    assert out.score == 0.7
    assert fit.calls == 2
    assert judge.calls == 2


def test_score_zero_fit() -> None:
    fit = _FakeFit([9.0])
    judge = _FakeJudge([_judge("accept", 0.0)])
    out = _scorer(fit, judge).score({"subject": "x", "nmck": 0}, "comp")
    assert out.score == 0.0


def test_score_no_normalization_uses_raw_fit() -> None:
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(normalize_fit_for_score=False, score_use_stub=False),
    )
    out = scorer.score({"subject": "x", "nmck": 100.0}, "comp")
    assert out.score == 8.0


def test_score_keeps_judge_final_over_fit() -> None:
    fit = _FakeFit([3.0, 3.0])
    judge = _FakeJudge([_judge("reject", 9.0, critics="занижено"), _judge("accept", 9.0)])
    out = _scorer(fit, judge).score({"subject": "x", "nmck": 10.0}, "comp")
    assert out.final_fit_score == 9.0
    assert out.score == 0.9
    assert fit.calls == 2


def test_score_propagates_requires_tz_review() -> None:
    class _FlaggedFit:
        def invoke(
            self,
            competencies: str,
            description: str,
            session_id: str | None = None,
            metadata: dict[str, object] | None = None,
            parent_config: object | None = None,
            run_name: str = "fit_scoring",
            truncated: bool = False,
            full_text: bool = False,
        ) -> FitResult:
            return _fit(5.0, requires_tz_review=True)

    judge = _FakeJudge([_judge("accept", 5.0)])
    scorer = Scorer(_FlaggedFit(), judge, Settings(score_use_stub=False))  # type: ignore[arg-type]
    out = scorer.score({"subject": "x", "nmck": 10.0}, "comp")
    assert out.requires_tz_review is True


def test_stub_returns_existing_score_without_chains() -> None:
    scorer = Scorer(None, None, Settings(score_use_stub=True))
    out = scorer.score({"subject": "x", "nmck": 5000.0, "score": 123.45}, "comp")
    assert out.score == 123.45
    assert out.judge.verdict == "accept"


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
            parent_config: object | None = None,
            run_name: str = "fit_scoring",
            truncated: bool = False,
            full_text: bool = False,
        ) -> FitResult:
            self.calls.append((session_id, metadata))
            return _fit(self._score)

    fit = _RecordingFit(8.0)
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(fit, judge, Settings(llm_model="gpt-test", score_use_stub=False))  # type: ignore[arg-type]
    scorer.score({"subject": "x", "nmck": 10.0}, "comp", procurement_id=42, run_id="run-1")

    assert fit.calls
    session_id, metadata = fit.calls[0]
    assert session_id == "run-1"
    assert metadata is not None
    assert metadata["procurement_id"] == 42
    assert metadata["run_id"] == "run-1"
    assert metadata["llm_model"] == "gpt-test"


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
            parent_config: object | None = None,
            run_name: str = "fit_scoring",
            truncated: bool = False,
            full_text: bool = False,
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


class _RecordingFit:
    """Fit, запоминающий переданное описание (для проверки подстановки ТЗ)."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = list(scores)
        self.descriptions: list[str] = []
        self.run_names: list[str] = []
        self.truncated_flags: list[bool] = []
        self.full_text_flags: list[bool] = []

    def invoke(
        self,
        competencies: str,
        description: str,
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
        parent_config: object | None = None,
        run_name: str = "fit_scoring",
        truncated: bool = False,
        full_text: bool = False,
    ) -> FitResult:
        self.descriptions.append(description)
        self.run_names.append(run_name)
        self.truncated_flags.append(truncated)
        self.full_text_flags.append(full_text)
        value = self._scores.pop(0) if self._scores else 5.0
        return _fit(value, requires_tz_review=(len(self.descriptions) == 1))


def _fake_tz_reviewer(description: str | None) -> object:
    class _Tz:
        def invoke(
            self,
            record: dict[str, object],
            parent_config: object,
            trace_meta: dict[str, object],
            session_id: str | None,
        ) -> TzReviewOutcome:
            return TzReviewOutcome(
                found=description is not None,
                file_name="ТЗ.docx",
                description=description,
                reason="ok",
            )

    return _Tz()


def test_score_uses_tz_text_when_requires_tz_review() -> None:
    """Если requires_tz_review и найден текст ТЗ — fit повторяется по тексту ТЗ."""
    fit = _RecordingFit([5.0, 8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False, tz_review_enabled=True),
        tz_reviewer=_fake_tz_reviewer("ТЗ: автоматизация документооборота"),  # type: ignore[arg-type]
    )
    out = scorer.score({"subject": "Сопровождение ПО", "nmck": 10.0}, "comp")

    assert len(fit.descriptions) == 2
    assert fit.descriptions[0] != fit.descriptions[1]
    assert fit.descriptions[1] == "ТЗ: автоматизация документооборота"
    assert fit.run_names == ["fit_scoring", "fit_tz"]
    # Повторный fit уже получил полный текст ТЗ — не запрашиваем чтение файла повторно.
    assert fit.full_text_flags == [False, True]
    assert out.requires_tz_review is False
    assert out.final_fit_score == 8.0
    assert out.description == "ТЗ: автоматизация документооборота"


def test_score_keeps_score_when_tz_not_found() -> None:
    """Если требует уточнение, но ТЗ не найден — скор без изменений, флаг сохранён."""
    fit = _RecordingFit([5.0, 8.0])
    judge = _FakeJudge([_judge("accept", 5.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False, tz_review_enabled=True),
        tz_reviewer=_fake_tz_reviewer(None),  # type: ignore[arg-type]
    )
    out = scorer.score({"subject": "Сопровождение ПО", "nmck": 10.0}, "comp")

    # ТЗ не найден — повторного fit нет, описание исходное.
    assert len(fit.descriptions) == 1
    assert out.requires_tz_review is True
    assert out.final_fit_score == 5.0


def test_score_keeps_score_when_tz_text_empty() -> None:
    """Пустой текст ТЗ трактуется как «не найден»: флаг сохраняется, скор без изменений."""
    fit = _RecordingFit([5.0])
    judge = _FakeJudge([_judge("accept", 5.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False, tz_review_enabled=True),
        tz_reviewer=_fake_tz_reviewer("   \n\t "),  # type: ignore[arg-type]
    )
    out = scorer.score({"subject": "Сопровождение ПО", "nmck": 10.0}, "comp")

    assert len(fit.descriptions) == 1
    assert out.requires_tz_review is True
    assert out.final_fit_score == 5.0


def test_score_skips_tz_when_flag_off() -> None:
    fit = _RecordingFit([5.0])
    judge = _FakeJudge([_judge("accept", 5.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False, tz_review_enabled=False),
        tz_reviewer=_fake_tz_reviewer("ТЗ-текст"),  # type: ignore[arg-type]
    )
    out = scorer.score({"subject": "Сопровождение ПО", "nmck": 10.0}, "comp")
    assert len(fit.descriptions) == 1
    assert out.requires_tz_review is True


class _HeaderRecordingFit:
    """Fit, возвращающий requires_tz_review=true + requires_tz_body=false на первом вызове."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = list(scores)
        self.descriptions: list[str] = []
        self.run_names: list[str] = []
        self.truncated_flags: list[bool] = []
        self.full_text_flags: list[bool] = []

    def invoke(
        self,
        competencies: str,
        description: str,
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
        parent_config: object | None = None,
        run_name: str = "fit_scoring",
        truncated: bool = False,
        full_text: bool = False,
    ) -> FitResult:
        self.descriptions.append(description)
        self.run_names.append(run_name)
        self.truncated_flags.append(truncated)
        self.full_text_flags.append(full_text)
        value = self._scores.pop(0) if self._scores else 5.0
        return _fit(
            value,
            requires_tz_review=(len(self.descriptions) == 1),
            requires_tz_body=(len(self.descriptions) != 1),
        )


def test_score_truncated_description_passes_truncated_flag() -> None:
    """Обрезанное многоточием описание передаёт truncated=True в первый fit."""
    fit = _RecordingFit([5.0])
    judge = _FakeJudge([_judge("accept", 5.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False, tz_review_enabled=False),
        tz_reviewer=_fake_tz_reviewer("ТЗ-текст"),  # type: ignore[arg-type]
    )
    scorer.score({"subject": "Разработка ПО и внедрение системы...", "nmck": 10.0}, "comp")
    assert fit.truncated_flags == [True]


def test_score_header_extends_description_from_tz() -> None:
    """requires_tz_body=false: описание расширяется заголовком из текста ТЗ."""
    tz_text = (
        "Разработка и внедрение системы автоматизации документооборота предприятия\n"
        "Общие положения..."
    )
    fit = _HeaderRecordingFit([5.0, 8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False, tz_review_enabled=True),
        tz_reviewer=_fake_tz_reviewer(tz_text),  # type: ignore[arg-type]
    )
    out = scorer.score(
        {"subject": "Разработка и внедрение системы автоматизации документооборота", "nmck": 10.0},
        "comp",
    )

    assert fit.run_names == ["fit_scoring", "fit_tz"]
    # Повторный fit уже получил текст (заголовок) из ТЗ — не запрашиваем чтение файла.
    assert fit.full_text_flags == [False, True]
    # Второй fit получает расширенный заголовок из ТЗ, а не весь текст ТЗ.
    assert fit.descriptions[1] == (
        "Разработка и внедрение системы автоматизации документооборота предприятия"
    )
    assert out.description == (
        "Разработка и внедрение системы автоматизации документооборота предприятия"
    )
    assert out.final_fit_score == 8.0


def test_score_header_falls_back_to_full_tz_when_not_found() -> None:
    """requires_tz_body=false, но фрагмент не найден — используется весь текст ТЗ."""
    tz_text = "Совершенно другой текст технического задания\n"
    fit = _HeaderRecordingFit([5.0, 8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(score_use_stub=False, tz_review_enabled=True),
        tz_reviewer=_fake_tz_reviewer(tz_text),  # type: ignore[arg-type]
    )
    out = scorer.score({"subject": "Разработка системы автоматизации", "nmck": 10.0}, "comp")
    assert fit.descriptions[1] == tz_text
    assert out.description == tz_text


class _FakeEmbedder:
    """Фейковый эмбеддер: возвращает вектор на основе первого текста."""

    def __init__(self, similarity: float = 0.7) -> None:
        self._similarity = similarity
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        # Векторы, косинусная близость которых равна self._similarity.
        return [[1.0, 0.0], [self._similarity, (1.0 - self._similarity**2) ** 0.5]]


def test_score_embedding_disabled_returns_none() -> None:
    """Ветка выключена (giga_enabled=False) — embedding_similarity=None, без эмбеддера."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    out = _scorer(fit, judge).score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")
    assert out.embedding_similarity is None
    assert out.score == 0.8


def test_score_embedding_missing_credentials_no_crash() -> None:
    """giga_enabled=True, но ключ доступа не задан — не падаем, ветка пропущена.

    Факт пропуска фиксируется в базовых метаданных (embedding_skipped), которые
    пишутся в LangFuse-трейс.
    """
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(
            score_use_stub=False,
            giga_enabled=True,
            giga_client_id="",
            giga_client_secret="",
        ),
    )  # type: ignore[arg-type]
    assert scorer._embedder is None  # noqa: SLF001
    assert scorer._base_metadata["embedding_skipped"] == "missing giga credentials"
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")
    assert out.embedding_similarity is None
    assert out.score == 0.8


def test_score_embedding_branch_runs_and_sets_similarity() -> None:
    """Ветка выполняется, similarity попадает в результат и влияет на score при alpha>0."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    embedder = _FakeEmbedder(similarity=0.6)
    scorer = Scorer(
        fit,
        judge,
        Settings(
            score_use_stub=False,
            giga_enabled=True,
            giga_client_id="cid",
            giga_client_secret="secret",
            giga_embedding_alpha=0.5,
            # Фильтрация выключена: ниже порога ветка не должна отсекать закупку.
            embedding_filter_threshold=0.0,
        ),
        embedder=embedder,  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")
    assert embedder.calls == 1
    assert out.embedding_similarity == 0.6
    # fit_norm = 8/10 = 0.8; base = 0.5*0.8 + 0.5*0.6 = 0.7; score = 0.7
    assert out.score == 0.7


def test_score_embedding_branch_sets_similarity_but_alpha_zero_keeps_score() -> None:
    """alpha=0 — similarity фиксируется, но на score не влияет."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(
            score_use_stub=False,
            giga_enabled=True,
            giga_client_id="c",
            giga_client_secret="s",
            embedding_filter_threshold=0.0,
        ),
        embedder=_FakeEmbedder(similarity=0.3),  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")
    assert out.embedding_similarity == 0.3
    assert out.score == 0.8


class _TextSensitiveEmbedder:
    """Эмбеддер с векторами, зависящими от текста (для проверки кэша).

    Компетенции дают [1, 0], любой другой текст — [0, 1]; запоминает все тексты,
    которые эмбеддились, чтобы проверить, что компетенции считались один раз.
    """

    def __init__(self) -> None:
        self.embedded_texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [[1.0, 0.0] if text == "компетенции" else [0.0, 1.0] for text in texts]


def test_score_embedding_competencies_embedded_once() -> None:
    """Вектор компетенций кэшируется: повторные закупки эмбеддят только описание."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0), _judge("accept", 8.0)])
    embedder = _TextSensitiveEmbedder()
    scorer = Scorer(
        fit,
        judge,
        Settings(
            score_use_stub=False,
            giga_enabled=True,
            giga_client_id="cid",
            giga_client_secret="secret",
            embedding_filter_threshold=0.0,
        ),
        embedder=embedder,  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    out1 = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")
    out2 = scorer.score({"subject": "Техподдержка", "nmck": 50.0}, "компетенции")

    # Компетенции эмбеддятся ровно один раз, описания — по разу на закупку.
    assert embedder.embedded_texts.count("компетенции") == 1
    assert len(embedder.embedded_texts) == 3
    assert embedder.embedded_texts[1:] == [out1.description, out2.description]
    # cosine([1,0], [0,1]) = 0 — оба прогона корректно вычислили близость.
    assert out1.embedding_similarity == 0.0
    assert out2.embedding_similarity == 0.0


def test_score_embedding_different_competencies_embedded_separately() -> None:
    """Разный текст компетенций — отдельный эмбеддинг (кэш ключуется по тексту)."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0), _judge("accept", 8.0)])
    embedder = _TextSensitiveEmbedder()
    scorer = Scorer(
        fit,
        judge,
        Settings(
            score_use_stub=False,
            giga_enabled=True,
            giga_client_id="cid",
            giga_client_secret="secret",
            embedding_filter_threshold=0.0,
        ),
        embedder=embedder,  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции A")
    scorer.score({"subject": "Техподдержка", "nmck": 50.0}, "компетенции B")

    assert embedder.embedded_texts.count("компетенции A") == 1
    assert embedder.embedded_texts.count("компетенции B") == 1
    assert len(embedder.embedded_texts) == 4


def _embedding_scorer(
    fit: object,
    judge: object,
    similarity: float,
    threshold: float,
) -> Scorer:
    return Scorer(
        fit,
        judge,
        Settings(
            score_use_stub=False,
            giga_enabled=True,
            giga_client_id="cid",
            giga_client_secret="secret",
            embedding_filter_threshold=threshold,
        ),
        embedder=_FakeEmbedder(similarity=similarity),  # type: ignore[arg-type]
    )  # type: ignore[arg-type]


def test_score_embedding_prefilter_below_threshold_skips_llm() -> None:
    """Близость ниже порога: LLM не запускается, fit_score=0, score_method=vector."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = _embedding_scorer(fit, judge, similarity=0.5, threshold=0.66)
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")

    assert fit.calls == 0
    assert judge.calls == 0
    assert out.score == 0.0
    assert out.fit_multiplier == 0.0
    assert out.final_fit_score == 0.0
    assert out.score_method == "vector"
    assert out.embedding_similarity == 0.5
    assert out.requires_tz_review is False


def test_score_embedding_prefilter_at_threshold_runs_llm() -> None:
    """Близость равна порогу: отсечения нет, LLM-пайплайн выполняется."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = _embedding_scorer(fit, judge, similarity=0.66, threshold=0.66)
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")

    assert fit.calls == 1
    assert judge.calls == 1
    assert out.score_method == "fit"
    assert out.embedding_similarity == 0.66


def test_score_embedding_prefilter_above_threshold_runs_llm() -> None:
    """Близость выше порога: LLM-пайплайн выполняется, score_method=fit."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = _embedding_scorer(fit, judge, similarity=0.8, threshold=0.66)
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")

    assert fit.calls == 1
    assert judge.calls == 1
    assert out.score == 0.8
    assert out.score_method == "fit"
    assert out.embedding_similarity == 0.8


def test_score_embedding_prefilter_disabled_threshold_zero() -> None:
    """Порог <= 0 отключает фильтрацию: низкая близость не отсекает закупку."""
    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = _embedding_scorer(fit, judge, similarity=0.1, threshold=0.0)
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")

    assert fit.calls == 1
    assert out.score == 0.8
    assert out.score_method == "fit"


def test_score_embedding_prefilter_branch_failure_runs_llm() -> None:
    """Сбой ветки эмбеддингов: фильтрация не применяется, LLM-пайплайн выполняется."""

    class _FailingEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding API down")

    fit = _FakeFit([8.0])
    judge = _FakeJudge([_judge("accept", 8.0)])
    scorer = Scorer(
        fit,
        judge,
        Settings(
            score_use_stub=False,
            giga_enabled=True,
            giga_client_id="cid",
            giga_client_secret="secret",
        ),
        embedder=_FailingEmbedder(),  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    out = scorer.score({"subject": "Разработка ПО", "nmck": 100.0}, "компетенции")

    assert fit.calls == 1
    assert out.embedding_similarity is None
    assert out.score == 0.8
    assert out.score_method == "fit"
