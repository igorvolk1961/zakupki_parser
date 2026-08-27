"""Операции репозитория с per-profile результатами скоринга (BR-07)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from zakupki_parser.storage.db import ProcurementEvaluation
from zakupki_parser.storage.repository.base import RepositoryMixin, _round_score

logger = logging.getLogger(__name__)


class EvaluationMixin(RepositoryMixin):
    """Per-profile оценки закупок (``procurement_evaluations``)."""

    @staticmethod
    async def _find_or_create_evaluation(
        session: Any, procurement_id: int, profile_id: int
    ) -> ProcurementEvaluation:
        """Find-or-create per-profile оценки в ОТКРЫТОЙ сессии (без commit)."""
        existing: ProcurementEvaluation | None = (
            await session.execute(
                select(ProcurementEvaluation).where(
                    ProcurementEvaluation.procurement_id == procurement_id,
                    ProcurementEvaluation.profile_id == profile_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ProcurementEvaluation(procurement_id=procurement_id, profile_id=profile_id)
            session.add(existing)
        return existing

    async def record_matched_keywords(
        self,
        procurement_id: int,
        profile_id: int,
        matched: list[str],
    ) -> None:
        """Записывает ключевые слова, по которым закупка отобрана профилем (R9).

        Оценка создаётся/обновляется find-or-create: поле ``matched_keywords``
        заполняется при сохранении закупки парсером (до внешнего скоринга).
        """
        if not matched:
            return
        async with self._db.session() as session:
            evaluation = await self._find_or_create_evaluation(session, procurement_id, profile_id)
            evaluation.matched_keywords = list(matched)
            await session.commit()
            logger.info(
                "Закупка %s: записаны matched_keywords профиля %s (%d)",
                procurement_id,
                profile_id,
                len(matched),
            )

    async def upsert_score(
        self,
        procurement_id: int,
        profile_id: int,
        *,
        score: float | None = None,
        fit_score: float | None = None,
        p_win: float | None = None,
        margin: float | None = None,
        score_method: str = "default",
        rag_report: dict[str, Any] | None = None,
        embedding_similarity: float | None = None,
        langfuse_trace_url: str | None = None,
    ) -> ProcurementEvaluation:
        """Обновляет/создаёт per-profile результат скоринга закупки."""
        async with self._db.session() as session:
            evaluation = await self._find_or_create_evaluation(session, procurement_id, profile_id)
            if score is not None:
                evaluation.score = _round_score(score)
            if fit_score is not None:
                evaluation.fit_score = _round_score(fit_score)
            if p_win is not None:
                evaluation.p_win = _round_score(p_win)
            if margin is not None:
                evaluation.margin = _round_score(margin)
            evaluation.score_method = score_method
            if embedding_similarity is not None:
                evaluation.embedding_similarity = embedding_similarity
            if langfuse_trace_url is not None:
                evaluation.langfuse_trace_url = langfuse_trace_url
            if rag_report is not None:
                evaluation.rag_report = rag_report
            await session.commit()
            return evaluation

    async def get_score(self, procurement_id: int, profile_id: int) -> ProcurementEvaluation | None:
        stmt = select(ProcurementEvaluation).where(
            ProcurementEvaluation.procurement_id == procurement_id,
            ProcurementEvaluation.profile_id == profile_id,
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def update_rag_report(
        self, procurement_id: int, profile_id: int, rag_report: dict[str, Any]
    ) -> ProcurementEvaluation:
        """Сохраняет RAG-отчёт анализа стоп-условий (не меняя score_method)."""
        async with self._db.session() as session:
            evaluation = await self._find_or_create_evaluation(session, procurement_id, profile_id)
            evaluation.rag_report = rag_report
            await session.commit()
        return evaluation

    async def fan_out_score(
        self,
        procurement_id: int,
        *,
        from_profile_id: int,
        score: float | None,
        fit_score: float | None,
        score_method: str,
        p_win: float | None = None,
        margin: float | None = None,
        embedding_similarity: float | None = None,
        langfuse_trace_url: str | None = None,
    ) -> int:
        """Раздаёт ОДИН общий скор всем профилям-участникам закупки (BR-07).

        Оценка считается один раз (против активного клиента) и копируется всем
        профилям, которые отобрали закупку (у них непустой ``matched_keywords``),
        кроме источника ``from_profile_id``: тогда каждый профиль видит этот агрегат
        в своей таблице. Возвращает число обновлённых профилей.
        """
        async with self._db.session() as session:
            participants = (
                (
                    await session.execute(
                        select(ProcurementEvaluation.profile_id).where(
                            ProcurementEvaluation.procurement_id == procurement_id,
                            ProcurementEvaluation.profile_id != from_profile_id,
                            ProcurementEvaluation.matched_keywords.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for pid in [p for p in participants if p is not None]:
                evaluation = await self._find_or_create_evaluation(session, procurement_id, pid)
                if score is not None:
                    evaluation.score = _round_score(score)
                if fit_score is not None:
                    evaluation.fit_score = _round_score(fit_score)
                if p_win is not None:
                    evaluation.p_win = _round_score(p_win)
                if margin is not None:
                    evaluation.margin = _round_score(margin)
                evaluation.score_method = score_method
                if embedding_similarity is not None:
                    evaluation.embedding_similarity = embedding_similarity
                if langfuse_trace_url is not None:
                    evaluation.langfuse_trace_url = langfuse_trace_url
            await session.commit()
        return len(participants)
