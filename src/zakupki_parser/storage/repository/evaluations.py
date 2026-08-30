"""Операции репозитория с per-profile результатами скоринга (BR-07)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from zakupki_parser.storage.db import ProcurementEvaluation
from zakupki_parser.storage.repository.base import RepositoryMixin, _round_score

logger = logging.getLogger(__name__)


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    """Рекурсивно слить ``src`` в ``dst`` (на месте).

    Для совпадающих ключей-словарей — рекурсивный merge; для остальных — простое
    перезаписывание ``dst[key] = value``. Используется для накопления стоимости
    обработки по этапам (scoring/analysis) в ``ProcurementEvaluation.costs``.
    """
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value


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
        comp_hash: str | None = None,
    ) -> None:
        """Записывает ключевые слова, по которым закупка отобрана профилем (R9).

        Оценка создаётся/обновляется find-or-create: поле ``matched_keywords``
        заполняется при сохранении закупки парсером (до внешнего скоринга).
        ``comp_hash`` — хэш канонического содержания компетенций профиля (BR-07):
        ключ дедупликации скоринга по идентичному содержанию компетенций.
        """
        if not matched:
            return
        async with self._db.session() as session:
            evaluation = await self._find_or_create_evaluation(session, procurement_id, profile_id)
            evaluation.matched_keywords = list(matched)
            if comp_hash is not None:
                evaluation.comp_hash = comp_hash
            await session.commit()
            logger.info(
                "Закупка %s: записаны matched_keywords профиля %s (%d)",
                procurement_id,
                profile_id,
                len(matched),
            )

    @staticmethod
    def _merge_costs_into(evaluation: ProcurementEvaluation, costs: dict[str, Any] | None) -> None:
        """Наложить стоимость (scoring/analysis) на ``evaluation.costs`` (в сессии).

        Merge (а не замена): этапы приходят разными вызовами POST /score и каждый
        обновляет свою ветку, не затирая соседнюю. Пустой ``costs`` — no-op.
        """
        if not costs:
            return
        current = dict(evaluation.costs or {})
        _deep_merge(current, costs)
        evaluation.costs = current

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
        costs: dict[str, Any] | None = None,
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
            self._merge_costs_into(evaluation, costs)
            await session.commit()
            return evaluation

    async def get_score(self, procurement_id: int, profile_id: int) -> ProcurementEvaluation | None:
        stmt = select(ProcurementEvaluation).where(
            ProcurementEvaluation.procurement_id == procurement_id,
            ProcurementEvaluation.profile_id == profile_id,
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def find_group_evaluation(
        self, procurement_id: int, comp_hash: str
    ) -> ProcurementEvaluation | None:
        """Оценка-«представитель» группы идентичного содержания компетенций (BR-07).

        Возвращает первое evaluation закупки с тем же ``comp_hash``, у которого уже
        есть результат скоринга (``fit_score IS NOT NULL``) либо задание поставлено
        (``scoring_queued_at IS NOT NULL``). Используется парсером для дедупликации:
        если такая пара есть — новое задание на эту закупку не ставится, профиль
        подписывается под результат группы.
        """
        stmt = (
            select(ProcurementEvaluation)
            .where(
                ProcurementEvaluation.procurement_id == procurement_id,
                ProcurementEvaluation.comp_hash == comp_hash,
            )
            .order_by(ProcurementEvaluation.id.asc())
        )
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        for row in rows:
            if row.fit_score is not None or row.scoring_queued_at is not None:
                return row
        return None

    async def apply_score_to_comp_hash_group(
        self,
        procurement_id: int,
        comp_hash: str,
        *,
        score: float | None = None,
        fit_score: float | None = None,
        p_win: float | None = None,
        margin: float | None = None,
        score_method: str = "default",
        embedding_similarity: float | None = None,
        langfuse_trace_url: str | None = None,
        rag_report: dict[str, Any] | None = None,
    ) -> int:
        """Распространяет результат скоринга на всех подписанных профилей группы.

        Применяется в обработчике POST /score: результат, посчитанный для
        представителя группы идентичного содержания компетенций, записывается
        каждому профилю, отобравшему закупку с тем же ``comp_hash``. Возвращает
        число обновлённых оценок.
        """
        async with self._db.session() as session:
            stmt = select(ProcurementEvaluation).where(
                ProcurementEvaluation.procurement_id == procurement_id,
                ProcurementEvaluation.comp_hash == comp_hash,
            )
            rows = list((await session.execute(stmt)).scalars().all())
            for evaluation in rows:
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
            return len(rows)

    async def update_rag_report(
        self,
        procurement_id: int,
        profile_id: int,
        rag_report: dict[str, Any],
        *,
        costs: dict[str, Any] | None = None,
    ) -> ProcurementEvaluation:
        """Сохраняет RAG-отчёт анализа стоп-условий (не меняя score_method)."""
        async with self._db.session() as session:
            evaluation = await self._find_or_create_evaluation(session, procurement_id, profile_id)
            evaluation.rag_report = rag_report
            self._merge_costs_into(evaluation, costs)
            await session.commit()
        return evaluation
