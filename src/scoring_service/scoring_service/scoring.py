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

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig, RunnableLambda

from scoring_service.llm_factory import build_llm, callbacks_for, langfuse_handler
from scoring_service.modules import embedding as embedding_module
from scoring_service.modules import margin as margin_module
from scoring_service.modules import p_win as p_win_module
from scoring_service.pipeline.description import (
    extend_description_from_tz,
    extract_description,
    is_truncated_description,
)
from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.pipeline.judge_chain import JudgeChain
from scoring_service.pipeline.tz_reviewer import TzReviewer, TzReviewOutcome
from scoring_service.schemas import FitResult, JudgeResult, ReasoningSteps, ScoringOutput
from scoring_service.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class _PipelineResult:
    """Результат основного LLM-пайплайна (fit/judge/refine)."""

    description: str
    fit: FitResult
    judge: JudgeResult
    final_fit: float
    pwin: float
    marg: float
    fit_norm: float
    requires_tz_review: bool
    requires_tz_body: bool


class Scorer:
    """Оркестратор: карточка + компетенции → полный результат скоринга."""

    def __init__(
        self,
        fit_chain: FitChain | None,
        judge_chain: JudgeChain | None,
        settings: Settings,
        callbacks: list[Any] | None = None,
        tz_reviewer: TzReviewer | None = None,
        embedder: Any | None = None,
        embedding_skip_reason: str | None = None,
    ) -> None:
        self._fit = fit_chain
        self._judge = judge_chain
        self._settings = settings
        # Callbacks для корневого run скоринга: один трейс на задание, в который
        # вложены fit/judge/refine как дочерние спаны (вместо отдельных трейсов).
        self._callbacks = callbacks
        # Параллельная ветка векторной близости (Giga Embedder). None, если ветка
        # выключена либо не настроен ключ доступа.
        self._embedder = embedder
        # Причина пропуска ветки (например, отсутствие ключа доступа) — пишется в
        # метаданные LangFuse-трейса, чтобы не было тихого «пропуска без следа».
        self._embedding_skip_reason = embedding_skip_reason
        # Уточнение по ТЗ работает только при tz_review_enabled=true (флаг имеет
        # приоритет над явно переданным reviewer — для тестов).
        self._tz_reviewer = (
            tz_reviewer
            if (settings.tz_review_enabled and tz_reviewer)
            else (TzReviewer(settings, callbacks) if settings.tz_review_enabled else None)
        )
        # Метаданные гиперпараметров/промптов, общие для всех вызовов скоринга.
        # Пишутся в каждый трейс, чтобы группировать/сравнивать запуски по конфигурации.
        self._base_metadata: dict[str, Any] = {
            "llm_model": settings.llm_model,
            "llm_temperature": settings.llm_temperature,
            "llm_structured_method": settings.llm_structured_method,
            "num_refine_rounds": settings.num_refine_rounds,
            "normalize_fit_for_score": settings.normalize_fit_for_score,
            "p_win": settings.p_win,
            "margin_rate": settings.margin_rate,
            "giga_enabled": settings.giga_enabled,
            "giga_configured": settings.giga_configured,
            "giga_model": settings.giga_embeddings_model,
            "giga_embedding_alpha": settings.giga_embedding_alpha,
        }
        if settings.giga_enabled and not settings.giga_configured:
            self._base_metadata["embedding_skipped"] = "missing giga credentials"

    def _trace_metadata(
        self,
        procurement_id: int | None,
        run_id: str | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Метаданные трассы: гиперпараметры + идентификаторы задания/запуска."""
        meta = dict(self._base_metadata)
        if procurement_id is not None:
            meta["procurement_id"] = procurement_id
        if run_id is not None:
            meta["run_id"] = run_id
        if extra:
            meta.update(extra)
        return meta

    def _refine_fit(
        self,
        competencies: str,
        description: str,
        critics: str,
        session_id: str | None,
        metadata: dict[str, Any],
        parent_config: RunnableConfig,
        full_text: bool = False,
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
            full_text=full_text,
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
            tz_review_necessity="",
            fit_score_rationale="stub",
        )
        fit = FitResult(
            reasoning=reasoning,
            fit_score=score,
            requires_tz_review=False,
            requires_tz_body=True,
        )
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
            requires_tz_review=False,
            requires_tz_body=True,
            fit_multiplier=score,
            p_win=p_win_module.p_win(record, self._settings),
            margin=margin_module.margin(record, self._settings),
            score=score,
        )

    def score(
        self,
        record: dict[str, Any],
        competencies: str,
        procurement_id: int | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScoringOutput:
        """Полный скоринг закупки по карточке и компетенциям.

        ``run_id`` — идентификатор запуска (батча): все задания одного запуска
        объединяются в одну LangFuse-сессию (``session_id``). Если ``run_id`` не задан,
        сессией служит ``procurement_id`` (для разовых/синхронных вызовов).

        Весь скоринг одного задания выполняется внутри единого корневого run
        (``scoring_job``), поэтому fit/judge/refine попадают в ОДИН трейс как дочерние
        спаны, а не в отдельные трейсы.
        """
        if self._settings.score_use_stub:
            return self._stub_score(record, procurement_id)

        session_id = run_id or (str(procurement_id) if procurement_id is not None else None)
        trace_meta = self._trace_metadata(procurement_id, run_id, metadata)
        root_config = cast(
            RunnableConfig,
            {
                "callbacks": self._callbacks or None,
                "run_name": "scoring_job",
                "metadata": {
                    **trace_meta,
                    **({"langfuse_session_id": session_id} if session_id is not None else {}),
                },
            },
        )
        runner = RunnableLambda(self._score_impl, name="scoring_job")
        return runner.invoke(
            (record, competencies, procurement_id, session_id, trace_meta, root_config),
            config=root_config,
        )

    def _score_impl(
        self,
        inputs: tuple[
            dict[str, Any],
            str,
            int | None,
            str | None,
            dict[str, Any],
            RunnableConfig,
        ],
        config: RunnableConfig | None = None,
    ) -> ScoringOutput:
        """Внутренняя реализация скоринга; выполняется внутри корневого run."""
        record, competencies, procurement_id, session_id, trace_meta, root_config = inputs
        parent_config = config or root_config
        description = extract_description(record)

        if self._embedder is not None:
            # Параллельная ветка векторной близости: эмбеддинги не зависят от
            # fit/judge, поэтому выполняются в отдельном потоке параллельно с
            # LLM-пайплайном. Ветка логируется в LangFuse как дочерний спан.
            branch = RunnableLambda(
                lambda _: embedding_module.embedding_similarity(
                    self._embedder, competencies, description
                ),
                name="embedding_similarity",
            )
            span_config = cast(
                RunnableConfig,
                {
                    **parent_config,
                    "metadata": {
                        **(parent_config.get("metadata") or {}),
                        "branch": "embedding",
                        "model": self._settings.giga_embeddings_model,
                        "alpha": self._settings.giga_embedding_alpha,
                    },
                },
            )

            def _run_branch() -> float:
                return branch.invoke(None, config=span_config)

            embed_sim: float | None = None
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_run_branch)
                try:
                    result = self._run_pipeline(
                        record,
                        competencies,
                        description,
                        session_id,
                        trace_meta,
                        parent_config,
                    )
                finally:
                    try:
                        embed_sim = fut.result(timeout=self._settings.giga_timeout_seconds)
                    except Exception:  # noqa: BLE001 - best-effort, не роняет скоринг
                        logger.exception("embedding branch failed for %s", procurement_id)
                        embed_sim = None
            return self._build_output(result, embed_sim, procurement_id)

        result = self._run_pipeline(
            record, competencies, description, session_id, trace_meta, parent_config
        )
        return self._build_output(result, None, procurement_id)

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

        # Уточнение по тексту ТЗ: если fit потребовал (requires_tz_review), ищем файл ТЗ
        # в карточке и извлекаем его текст. Если найден — повторный fit/judge выполняются
        # по расширенному описанию вместо обрезанного. Иначе скор остаётся без изменений.
        tz_outcome: TzReviewOutcome | None = None
        effective_description = description
        # True, только если уточнение реально состоялось (ТЗ найден и текст непустой).
        tz_refined = False
        # True, когда модели уже предоставлен полный текст ТЗ: дальше не запрашиваем
        # повторное чтение файла (requires_tz_review/requires_tz_body).
        full_text = False
        if fit.requires_tz_review and self._tz_reviewer is not None:
            tz_outcome = self._tz_reviewer.invoke(record, parent_config, trace_meta, session_id)
            if tz_outcome.found and tz_outcome.description and tz_outcome.description.strip():
                tz_refined = True
                if not fit.requires_tz_body:
                    # Достаточно заголовка (тело ТЗ не нужно): пытаемся алгоритмически
                    # расширить обрезанное описание фрагментом из полного текста ТЗ.
                    # Если не нашли — читаем всё тело ТЗ.
                    effective_description = (
                        extend_description_from_tz(description, tz_outcome.description)
                        or tz_outcome.description
                    )
                else:
                    effective_description = tz_outcome.description
                full_text = True
                fit = self._fit.invoke(  # type: ignore[union-attr]
                    competencies,
                    effective_description,
                    session_id,
                    trace_meta,
                    parent_config=parent_config,
                    run_name="fit_tz",
                    full_text=True,
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
                full_text=full_text,
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
        pwin = p_win_module.p_win(record, self._settings)
        marg = margin_module.margin(record, self._settings)
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
            pwin=pwin,
            marg=marg,
            fit_norm=fit_norm,
            # Флаг остаётся, если уточнение не запрошено или не состоялось
            # (ТЗ не найден / текст пуст) — скор не уточнён. При успешном
            # уточнении снимаем флаг.
            requires_tz_review=(
                fit.requires_tz_review if tz_outcome is None or not tz_refined else False
            ),
            requires_tz_body=(
                fit.requires_tz_body if tz_outcome is None or not tz_refined else True
            ),
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
        score = round(base * result.pwin * result.marg, self._settings.score_round_digits)

        return ScoringOutput(
            procurement_id=procurement_id,
            description=result.description,
            fit=result.fit,
            judge=result.judge,
            final_fit_score=result.final_fit,
            requires_tz_review=result.requires_tz_review,
            requires_tz_body=result.requires_tz_body,
            fit_multiplier=result.fit_norm,
            p_win=result.pwin,
            margin=result.marg,
            score=score,
            embedding_similarity=embed_sim,
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
    # Параллельная ветка векторной близости. Строится только при включённом флаге
    # и заданном ключе доступа; иначе — None с причиной пропуска (без падения).
    embedder: Any | None = None
    embedding_skip_reason: str | None = None
    if settings.giga_enabled:
        if settings.giga_configured:
            from scoring_service.modules.giga_embedder import GigaEmbedder, GigaTokenProvider

            token_provider = GigaTokenProvider(
                auth_url=settings.giga_auth_url,
                client_id=settings.giga_client_id,
                client_secret=settings.giga_client_secret,
                scope=settings.giga_auth_scope,
                min_ttl_seconds=settings.giga_min_token_ttl_seconds,
                verify_ssl=settings.giga_verify_ssl,
            )
            embedder = GigaEmbedder(
                base_url=settings.giga_base_url,
                model=settings.giga_embeddings_model,
                token_provider=token_provider,
                verify_ssl=settings.giga_verify_ssl,
            )
        else:
            embedding_skip_reason = "missing giga credentials"
    return Scorer(
        FitChain(llm, callbacks, method=settings.llm_structured_method),
        JudgeChain(llm, callbacks, method=settings.llm_structured_method),
        settings,
        callbacks=callbacks,
        embedder=embedder,
        embedding_skip_reason=embedding_skip_reason,
    )
