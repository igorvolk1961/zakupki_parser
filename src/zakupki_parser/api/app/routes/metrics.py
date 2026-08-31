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
from datetime import datetime
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


def _parse_dt(value: str | None) -> datetime | None:
    """ISO-строка -> datetime (None при отсутствии/ошибке)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cycle_record(
    evaluation_id: int,
    procurement_id: int,
    number: str | None,
    subject: str | None,
    created_at: str | None,
    costs: dict[str, Any],
    *,
    iteration: int | None = None,
    platform: str | None = None,
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
        "iteration": iteration,
        "platform": platform,
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
        # Признак «скоринг выполнялся» (для включения цикла в статистику скоринга).
        "has_scoring": bool(scoring),
    }


def _avg(values: Sequence[float | None]) -> float | None:
    """Среднее по непустым значениям; None, если нечего усреднять."""
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _rec_order(record: dict[str, Any]) -> tuple[bool, int, str]:
    """Порядок для группировки: сначала реальная итерация (по номеру), затем легаси.

    Батч = (итерация, площадка): в одном проходе планировщика параллельно
    обрабатывается несколько площадок, поэтому группируем по паре, чтобы каждая
    строка журнала несла свою площадку и итерацию. Легаси (без итерации) — в конец.
    """
    iteration = record.get("iteration")
    platform = record.get("platform") or ""
    return (iteration is None, iteration or 0, platform)


def _batch_row(batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Строка журнала батча: сумма стоимостей и среднее остальных метрик."""
    cost_scoring = round(sum(r["cost_scoring"] for r in batch), 8)
    cost_analysis = round(sum(r["cost_analysis"] for r in batch), 8)
    llm_tokens = [r["llm"]["tokens"] for r in batch if r["llm"]]
    llm_latency = [r["llm"]["latency_ms"] for r in batch if r["llm"]]
    emb_tokens = [r["embeddings"]["tokens"] for r in batch if r["embeddings"]]
    emb_latency = [r["embeddings"]["latency_ms"] for r in batch if r["embeddings"]]
    return {
        "started_at": batch[0]["created_at"],
        "ended_at": batch[-1]["created_at"],
        "iteration": batch[0]["iteration"],
        "platform": batch[0]["platform"],
        "count": len(batch),
        "cost_scoring": cost_scoring,
        "cost_analysis": cost_analysis,
        "cost_total": round(cost_scoring + cost_analysis, 8),
        "tokens_scoring": _avg([r["tokens_scoring"] for r in batch]),
        "tokens_analysis": _avg([r["tokens_analysis"] for r in batch]),
        "tokens_total": _avg([r["tokens_total"] for r in batch]),
        "llm": (
            {"tokens": _avg(llm_tokens), "latency_ms": _avg(llm_latency)}
            if llm_tokens or llm_latency
            else None
        ),
        "embeddings": (
            {"tokens": _avg(emb_tokens), "latency_ms": _avg(emb_latency)}
            if emb_tokens or emb_latency
            else None
        ),
        "scoring_calls": _avg([r["scoring_calls"] for r in batch]),
        "duration_ms": _avg([r["duration_ms"] for r in batch]),
    }


def build_metrics_journal(
    cycles: list[dict[str, Any]],
    *,
    limit: int | None = None,
    batch_gap_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Собрать журнал метрик: батчи + статистика скоринга + расходы по датам.

    ``cycles`` — список словарей с ключами ``evaluation_id``, ``procurement_id``,
    ``number``, ``subject``, ``created_at`` (ISO), ``costs`` (dict). Обрабатываются
    только циклы с непустой стоимостью; остальные отбрасываются.

    ``batch_gap_seconds`` — пауза цикла парсера (``ops.timeout_seconds``, по умолчанию
    1 час): зазор по времени между последовательными циклами больше порога начинает
    новый батч. Строка журнала — один батч: сумма стоимостей (скоринг/анализ/всего)
    и среднее остальных метрик (токены, латенси, время выполнения).

    Средние считаются по одному проходу по записям, отсортированным по времени:
    батч копится по мере итерации, а его итоговая строка (сумма стоимостей и среднее
    метрик) формируется в момент перехода к следующему батчу (когда зазор превышает
    паузу) — без отдельной группировки до расчёта.
    """
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

    records: list[dict[str, Any]] = []
    for cycle in cycles:
        costs = cycle.get("costs") or {}
        if not costs:
            continue
        records.append(
            _cycle_record(
                int(cycle["evaluation_id"]),
                int(cycle["procurement_id"]),
                cycle.get("number"),
                cycle.get("subject"),
                cycle.get("created_at"),
                costs,
                iteration=cycle.get("iteration"),
                platform=cycle.get("platform"),
            )
        )

    batch_rows: list[dict[str, Any]] = []
    current_batch: list[dict[str, Any]] = []
    prev_ts: datetime | None = None

    for record in sorted(records, key=lambda r: (_rec_order(r), r["created_at"] or "")):
        ts = _parse_dt(record["created_at"])
        head_key = (
            (
                current_batch[0]["iteration"],
                current_batch[0]["platform"],
            )
            if current_batch
            else None
        )
        # Граница батча: сменилась пара (итерация, площадка) — либо у легаси-записей
        # (без итерации) зазор по времени превысил паузу цикла.
        if current_batch and (
            (record["iteration"], record["platform"]) != head_key
            or (
                head_key is not None
                and head_key[0] is None
                and prev_ts is not None
                and ts is not None
                and (ts - prev_ts).total_seconds() > float(batch_gap_seconds)
            )
        ):
            batch_rows.append(_batch_row(current_batch))
            current_batch = []
        current_batch.append(record)
        prev_ts = ts

        date_key = (record["created_at"] or "")[:10]
        if date_key:
            day = by_date[date_key]
            day["scoring_usd"] += record["cost_scoring"]
            day["analysis_usd"] += record["cost_analysis"]
            day["total_usd"] += record["cost_total"]
            day["scoring_tokens"] += int(record["tokens_scoring"])
            day["analysis_tokens"] += int(record["tokens_analysis"])
            day["total_tokens"] += int(record["tokens_total"])

        if not record["has_scoring"]:
            continue
        cost_values.append(record["cost_scoring"])
        duration_values.append(float(record["duration_ms"]))
        token_values.append(int(record["tokens_scoring"]))
        if record["llm"]:
            llm_tokens.append(int(record["llm"]["tokens"]))
            llm_latency.append(float(record["llm"]["latency_ms"]))
        if record["embeddings"]:
            emb_tokens.append(int(record["embeddings"]["tokens"]))
            emb_latency.append(float(record["embeddings"]["latency_ms"]))

    # Последний (незакрытый) батч.
    if current_batch:
        batch_rows.append(_batch_row(current_batch))
    # Журнал — последние батчи первыми.
    batch_rows.reverse()

    by_date_list = [
        {"date": date_key, **day} for date_key, day in sorted(by_date.items(), reverse=True)
    ]

    return {
        "total_batches": len(batch_rows),
        "batches": batch_rows[:limit] if limit else batch_rows,
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
        """Журнал батчей + статистика скоринга и расходы на токены по датам.

        Граница батча — пауза цикла парсера (``ops.timeout_seconds``, по умолчанию
        1 час): закупки, обработанные подряд до этой паузы (один проход парсера),
        образуют один батч.
        """
        repo = ctx._repo()
        # Реальная пауза между проходами планировщика (run_service → wait timeout_seconds):
        # именно она отделяет закупки одного прохода от следующего. recovery_ttl_seconds
        # тоже равен 1 часу по умолчанию, но это порог повторной постановки, а не пауза.
        batch_gap_seconds = float(ctx.state.cfg.ops.timeout_seconds)
        rows = await repo.list_costed_evaluations()
        cycles = [
            {
                "evaluation_id": evaluation.id,
                "procurement_id": evaluation.procurement_id,
                "number": number,
                "subject": subject,
                "platform": platform,
                "iteration": evaluation.iteration,
                "created_at": (
                    evaluation.created_at.isoformat() if evaluation.created_at else None
                ),
                "costs": evaluation.costs or {},
            }
            for evaluation, number, subject, platform in rows
        ]
        return build_metrics_journal(cycles, limit=limit, batch_gap_seconds=batch_gap_seconds)

    return router
