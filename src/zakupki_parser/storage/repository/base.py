"""Общие хелперы репозитория (округление score, per-profile подзапросы)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from zakupki_parser.storage.db import Database, Procurement, ProcurementEvaluation


def _round_score(value: Any) -> float | None:
    """Округляет score до копеек (0.01 ₽) перед записью в БД."""
    if value is None:
        return None
    return round(float(value), 2)


def effective_is_active(
    is_active: bool, deadline: datetime | None, now: datetime | None = None
) -> bool:
    """Эффективная активность на стороне клиента.

    Активна, если закупка активна по статусу (``is_active``) И срок актуальности
    не истёк (``deadline`` отсутствует или не раньше ``now``).
    """
    if not is_active:
        return False
    if deadline is None:
        return True
    return deadline >= (now or datetime.now(UTC))


def _profile_score_subquery(profile_id: int) -> Any:
    """Per-profile подзапрос скоринга (фильтр/сортировка по fit_score и score_method)."""
    return (
        select(
            ProcurementEvaluation.procurement_id.label("procurement_id"),
            ProcurementEvaluation.fit_score.label("fit_score"),
            ProcurementEvaluation.score_method.label("score_method"),
        )
        .where(ProcurementEvaluation.profile_id == profile_id)
        .subquery()
    )


def _apply_profile_score(
    row: Procurement, evaluations: list[ProcurementEvaluation], profile_id: int
) -> None:
    """Налагает per-profile результат скоринга на карточку для API-ответа.

    Если для закупки есть оценка под указанного профиль (контекст компетенций/
    вопросов) — базовые колонки ``procurements`` (дефолтный скор) заменяются
    per-profile значениями, а ``rag_report`` подкладывается динамическим атрибутом.
    """
    for evaluation in evaluations:
        if evaluation.profile_id == profile_id:
            row.score = evaluation.score
            row.fit_score = evaluation.fit_score
            row.p_win = evaluation.p_win
            row.margin = evaluation.margin
            row.score_method = evaluation.score_method
            row.embedding_similarity = evaluation.embedding_similarity
            row.langfuse_trace_url = evaluation.langfuse_trace_url
            # rag_report — per-user, колонки в procurements нет: подкладываем
            # динамическим атрибутом для API-ответа (ClassVar на Procurement).
            row.rag_report = evaluation.rag_report
            return


class RepositoryMixin:
    """База доменных миксинов репозитория: общий доступ к ``Database``."""

    _db: Database

    def __init__(self, db: Database) -> None:
        self._db = db
