"""Ветка векторной близости (Giga Embedder) и результат предфильтрации.

Миксин, используемый классом ``Scorer``: ветка выполняется ДО LLM-пайплайна
и используется для предварительной фильтрации закупок (``score_method=sim``).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from langchain_core.runnables import RunnableConfig, RunnableLambda

from scoring_service.modules import embedding as embedding_module
from scoring_service.schemas import FitResult, JudgeResult, ReasoningSteps, ScoringOutput
from scoring_service.settings import Settings

logger = logging.getLogger(__name__)


class EmbeddingMixin:
    """Векторная ветка: близость описания к компетенциям + результат предфильтрации."""

    # Атрибуты задаются в Scorer.__init__ (см. scoring/__init__.py); объявлены
    # здесь, чтобы mypy видел их в миксине.
    _embedder: Any | None
    _competencies_embedding_cache: dict[str, list[float]]
    _settings: Settings

    def _run_embedding_branch(
        self,
        competencies: str,
        description: str,
        parent_config: RunnableConfig,
    ) -> float | None:
        """Ветка векторной близости (best-effort): None при сбое, не роняет скоринг."""
        embedder = self._embedder
        if embedder is None:
            return None
        branch = RunnableLambda(
            lambda _: embedding_module.embedding_similarity(
                embedder, competencies, description, self._competencies_embedding_cache
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
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(branch.invoke, None, config=span_config)
                return fut.result(timeout=self._settings.giga_timeout_seconds)
        except Exception:  # noqa: BLE001 - best-effort, не роняет скоринг
            logger.exception("embedding branch failed")
            return None

    def _filtered_output(
        self,
        description: str,
        embed_sim: float,
        procurement_id: int | None,
    ) -> ScoringOutput:
        """Результат предварительной фильтрации: LLM не выполнялся, fit_score=0."""
        reasoning = ReasoningSteps(
            procurement_essence="",
            competencies_essence="",
            relevant_competencies="",
            term_overlap_mismatch_check="",
            synonym_semantic_bridge="",
            uncovered_scope="",
            tz_review_necessity="",
            fit_score_rationale="pre-filtered by embedding similarity",
        )
        fit = FitResult(
            reasoning=reasoning,
            fit_score=0.0,
            requires_tz_review=False,
            requires_tz_body=False,
        )
        judge = JudgeResult(
            critics="Предварительная фильтрация: векторная близость ниже порога",
            verdict="accept",
            final_fit_score=0.0,
        )
        return ScoringOutput(
            procurement_id=procurement_id,
            description=description,
            fit=fit,
            judge=judge,
            final_fit_score=0.0,
            requires_tz_review=False,
            requires_tz_body=False,
            fit_multiplier=0.0,
            score=0.0,
            score_method="sim",
            embedding_similarity=embed_sim,
        )
