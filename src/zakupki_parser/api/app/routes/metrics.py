"""Метрики обработки закупок для роли analyst (вкладка «Метрики»).

Журнал затрат по отдельным циклам скоринга/анализа (``procurement_evaluations``),
сводная статистика (средние и разброс) ключевых метрик скоринга раздельно для
LLM и эмбеддингов и журнал расходов на токены по датам.

Данные берутся из ``costs`` (JSONB) карточек оценок: объект ``{"scoring": ...,
"analysis": ...}``, где у каждого этапа есть ``usd``, ``tokens``, ``latency_ms``,
``duration_ms`` и (после перехода к раздельным метрикам) вложенный ``components``
с ``llm``/``embeddings``. Агрегация — чистая функция ``build_metrics_journal``,
чтобы её можно было тестировать без БД.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query

from zakupki_parser.api.app.deps import ApiContext


def _f(v: Any) -> float:
    """Безопасное float (0 при отсутствии/ошибке)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _n(v: Any) -> int:
    """Безопасное int (0 при отсутствии/ошибке)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _stats(values: Sequence[float | None]) -> dict[str, Any]:
    """Средние и разброс списка значений: count/sum/avg/min/max/std."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"count": 0, "sum": 0.0, "avg": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    n = len(vals)
    total = sum(vals)
    avg = total / n
    variance = sum((v - avg) ** 2 for v in vals) / n
    return {
        "count": n,
        "sum": round(total, 6),
        "avg": round(avg, 6),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
        "std": round(variance**0.5, 6),
    }


def _component(costs: dict[str, Any], stage: str, name: str) -> dict[str, Any]:
    """Метрики компонента (llm/embeddings) стадии; {} если не сохранены."""
    return ((costs.get(stage) or {}).get("components") or {}).get(name) or {}


def _cycle_record(
    evaluation_id: int,
    procurement_id: int,
    number: str | None,
    subject: str | None,
    created_at: str | None,
    costs: dict[str, Any],
) -> dict[str, Any]:
    """Одна строка журнала: стоимость цикла и метрики скоринга/анализа."""
    scoring = costs.get("scoring") or {}
    analysis = costs.get("analysis") or {}
    scoring_usd = _f(scoring.get("usd"))
    analysis_usd = _f(analysis.get("usd"))
    scoring_tokens = _n((scoring.get("tokens") or {}).get("total"))
    analysis_tokens = _n((analysis.get("tokens") or {}).get("total"))
    llm_comp = _component(costs, "scoring", "llm")
    emb_comp = _component(costs, "scoring", "embeddings")
    return {
        "evaluation_id": evaluation_id,
        "procurement_id": procurement_id,
        "number": number,
        "subject": subject,
        "created_at": created_at,
        "cost_scoring": round(scoring_usd, 8),
        "cost_analysis": round(analysis_usd, 8),
        "cost_total": round(scoring_usd + analysis_usd, 8),
        "tokens_scoring": scoring_tokens,
        "tokens_analysis": analysis_tokens,
        "tokens_total": scoring_tokens + analysis_tokens,
        "llm": (
            {
                "tokens": _n((llm_comp.get("tokens") or {}).get("total")),
                "latency_ms": round(_f(llm_comp.get("latency_ms")), 3),
            }
            if llm_comp
            else None
        ),
        "embeddings": (
            {
                "tokens": _n((emb_comp.get("tokens") or {}).get("total")),
                "latency_ms": round(_f(emb_comp.get("latency_ms")), 3),
            }
            if emb_comp
            else None
        ),
        "scoring_calls": _n(scoring.get("calls")),
        "duration_ms": round(_f(scoring.get("duration_ms")), 3),
    }


def build_metrics_journal(
    cycles: list[dict[str, Any]],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Собрать журнал метрик: строки циклов + статистика скоринга + расходы по датам.

    ``cycles`` — список словарей с ключами ``evaluation_id``, ``procurement_id``,
    ``number``, ``subject``, ``created_at`` (ISO), ``costs`` (dict). Обрабатываются
    только циклы с непустой стоимостью; остальные отбрасываются.
    """
    records: list[dict[str, Any]] = []
    by_date: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "scoring_usd": 0.0,
            "analysis_usd": 0.0,
            "total_usd": 0.0,
            "scoring_tokens": 0,
            "analysis_tokens": 0,
            "total_tokens": 0,
        }
    )
    cost_values: list[float] = []
    duration_values: list[float] = []
    token_values: list[int] = []
    llm_tokens: list[int] = []
    llm_latency: list[float] = []
    emb_tokens: list[int] = []
    emb_latency: list[float] = []

    for cycle in cycles:
        costs = cycle.get("costs") or {}
        if not costs:
            continue
        record = _cycle_record(
            int(cycle["evaluation_id"]),
            int(cycle["procurement_id"]),
            cycle.get("number"),
            cycle.get("subject"),
            cycle.get("created_at"),
            costs,
        )
        records.append(record)

        date_key = (record["created_at"] or "")[:10]
        if date_key:
            day = by_date[date_key]
            day["scoring_usd"] += record["cost_scoring"]
            day["analysis_usd"] += record["cost_analysis"]
            day["total_usd"] += record["cost_total"]
            day["scoring_tokens"] += int(record["tokens_scoring"])
            day["analysis_tokens"] += int(record["tokens_analysis"])
            day["total_tokens"] += int(record["tokens_total"])

        scoring = costs.get("scoring") or {}
        if not scoring:
            continue
        cost_values.append(record["cost_scoring"])
        duration_values.append(_f(scoring.get("duration_ms")))
        token_values.append(int(record["tokens_scoring"]))
        if record["llm"]:
            llm_tokens.append(int(record["llm"]["tokens"]))
            llm_latency.append(float(record["llm"]["latency_ms"]))
        if record["embeddings"]:
            emb_tokens.append(int(record["embeddings"]["tokens"]))
            emb_latency.append(float(record["embeddings"]["latency_ms"]))

    by_date_list = [
        {"date": date_key, **day} for date_key, day in sorted(by_date.items(), reverse=True)
    ]

    return {
        "total_cycles": len(records),
        "cycles": records[:limit] if limit else records,
        "scoring_stats": {
            "count": len(cost_values),
            "cost": _stats(cost_values),
            "tokens": _stats(token_values),
            "duration_ms": _stats(duration_values),
            "llm": {"tokens": _stats(llm_tokens), "latency_ms": _stats(llm_latency)},
            "embeddings": {"tokens": _stats(emb_tokens), "latency_ms": _stats(emb_latency)},
        },
        "by_date": by_date_list,
    }


def build_metrics_router(ctx: ApiContext) -> APIRouter:
    """Роутер метрик обработки (только роль analyst)."""

    router = APIRouter(tags=["metrics"])

    @router.get(
        "/api/metrics",
        dependencies=[Depends(ctx.require_analyst)],
    )
    async def get_metrics(limit: int | None = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
        """Журнал циклов + статистика скоринга и расходы на токены по датам."""
        repo = ctx._repo()
        rows = await repo.list_costed_evaluations()
        cycles = [
            {
                "evaluation_id": evaluation.id,
                "procurement_id": evaluation.procurement_id,
                "number": number,
                "subject": subject,
                "created_at": (
                    evaluation.created_at.isoformat() if evaluation.created_at else None
                ),
                "costs": evaluation.costs or {},
            }
            for evaluation, number, subject in rows
        ]
        return build_metrics_journal(cycles, limit=limit)

    return router
