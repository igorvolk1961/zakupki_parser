"""Тесты журнала метрик обработки (вкладка «Метрики» для аналитика)."""

from __future__ import annotations

from typing import Any

from zakupki_parser.api.app.routes.metrics import build_metrics_journal


def _cycle(
    eid: int,
    pid: int,
    number: str,
    created_at: str,
    costs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evaluation_id": eid,
        "procurement_id": pid,
        "number": number,
        "subject": "Предмет",
        "created_at": created_at,
        "costs": costs,
    }


def _scoring(
    usd: float, tokens: int, latency: float, duration: float, llm: int, emb: int
) -> dict[str, Any]:
    return {
        "usd": usd,
        "calls": 2,
        "latency_ms": latency,
        "duration_ms": duration,
        "tokens": {"total": tokens},
        "components": {
            "llm": {"tokens": {"total": llm}, "latency_ms": latency - 100.0},
            "embeddings": {"tokens": {"total": emb}, "latency_ms": 100.0},
        },
    }


def test_journal_cycles_and_stats() -> None:
    cycles = [
        _cycle(
            1,
            10,
            "А",
            "2026-08-30T10:00:00+00:00",
            {"scoring": _scoring(0.0026, 10983, 31500.0, 31525.0, 9983, 1000)},
        ),
        _cycle(
            2,
            11,
            "Б",
            "2026-08-30T11:00:00+00:00",
            {"scoring": _scoring(0.0014, 8000, 20000.0, 20010.0, 7000, 1000)},
        ),
        _cycle(
            3,
            12,
            "В",
            "2026-08-31T09:00:00+00:00",
            {
                "scoring": _scoring(0.0008, 4000, 9000.0, 9010.0, 3500, 500),
                "analysis": {
                    "usd": 0.001,
                    "calls": 1,
                    "tokens": {"total": 3000},
                    "latency_ms": 4000.0,
                    "duration_ms": 4010.0,
                },
            },
        ),
    ]
    r = build_metrics_journal(cycles)
    assert r["total_cycles"] == 3
    assert len(r["cycles"]) == 3
    # Циклы: подсчёт стоимости скоринга/анализа/всего.
    assert r["cycles"][0]["number"] == "А"
    assert r["cycles"][0]["cost_scoring"] == 0.0026
    assert r["cycles"][0]["cost_total"] == 0.0026
    assert r["cycles"][0]["llm"]["tokens"] == 9983
    assert r["cycles"][0]["embeddings"]["latency_ms"] == 100.0
    # Цикл с анализом: стоимость анализа учитывается в total.
    assert r["cycles"][2]["cost_analysis"] == 0.001
    assert r["cycles"][2]["cost_total"] == 0.0018
    # Статистика скоринга: среднее/мин/макс стоимости.
    s = r["scoring_stats"]
    assert s["count"] == 3
    assert s["cost"]["avg"] == round((0.0026 + 0.0014 + 0.0008) / 3, 6)
    assert s["cost"]["min"] == 0.0008
    assert s["cost"]["max"] == 0.0026
    assert s["llm"]["tokens"]["count"] == 3
    assert s["llm"]["tokens"]["avg"] == round((9983 + 7000 + 3500) / 3, 6)
    assert s["embeddings"]["latency_ms"]["count"] == 3
    assert s["duration_ms"]["max"] == 31525.0
    # Журнал по датам: суммирование токенов/стоимости.
    by = {d["date"]: d for d in r["by_date"]}
    assert by["2026-08-31"]["scoring_tokens"] == 4000
    assert by["2026-08-31"]["analysis_tokens"] == 3000
    assert by["2026-08-30"]["scoring_tokens"] == 10983 + 8000
    assert by["2026-08-30"]["total_tokens"] == 10983 + 8000
    assert by["2026-08-31"]["total_tokens"] == 4000 + 3000


def test_journal_without_components_uses_stage_totals() -> None:
    # Старые данные без `components`: метрики компонентов отсутствуют, но
    # стоимость/токены/время станции считаются на уровне стадии.
    cycles = [
        _cycle(
            1,
            10,
            "А",
            "2026-08-30T10:00:00+00:00",
            {
                "scoring": {
                    "usd": 0.0025,
                    "calls": 3,
                    "latency_ms": 31506.0,
                    "duration_ms": 31525.0,
                    "tokens": {"total": 9983},
                }
            },
        )
    ]
    r = build_metrics_journal(cycles)
    cycle = r["cycles"][0]
    assert cycle["llm"] is None and cycle["embeddings"] is None
    assert cycle["tokens_scoring"] == 9983
    assert r["scoring_stats"]["count"] == 1
    assert r["scoring_stats"]["tokens"]["avg"] == 9983.0
    assert r["scoring_stats"]["llm"]["tokens"]["count"] == 0


def test_journal_empty() -> None:
    r = build_metrics_journal([])
    assert r["total_cycles"] == 0
    assert r["cycles"] == []
    assert r["scoring_stats"]["count"] == 0
    assert r["by_date"] == []


def test_journal_skips_empty_costs_and_limits_cycles() -> None:
    cycles = [
        _cycle(1, 10, "А", "2026-08-30T10:00:00+00:00", {}),  # без стоимости — пропускается
        _cycle(
            2,
            11,
            "Б",
            "2026-08-30T11:00:00+00:00",
            {"scoring": _scoring(0.001, 100, 10.0, 12.0, 90, 10)},
        ),
    ]
    r = build_metrics_journal(cycles, limit=1)
    assert r["total_cycles"] == 1
    assert len(r["cycles"]) == 1
