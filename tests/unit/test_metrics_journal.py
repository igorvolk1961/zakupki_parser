"""Тесты журнала метрик обработки (вкладка «Метрики» для аналитика).

Строки журнала — батчи: закупки, обработанные подряд до задержки повтора
(``batch_gap_seconds``, по умолчанию 1 час = 3600 с). Внутри батча суммируются
стоимости, остальные метрики усредняются.
"""

from __future__ import annotations

from typing import Any

from zakupki_parser.api.app.routes.metrics import build_metrics_journal

GAP = 3600.0


def _cycle(
    eid: int,
    number: str,
    created_at: str,
    costs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evaluation_id": eid,
        "procurement_id": eid,
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


def test_journal_batches_sum_cost_avg_metrics() -> None:
    # Три цикла: первые два — подряд (зазор 1800 с < 3600), третий — новый батч
    # (зазор 4200 с от второго).
    cycles = [
        _cycle(
            1,
            "А",
            "2026-08-30T10:00:00+00:00",
            {"scoring": _scoring(0.0026, 10983, 31500.0, 31525.0, 9983, 1000)},
        ),
        _cycle(
            2,
            "Б",
            "2026-08-30T10:30:00+00:00",
            {"scoring": _scoring(0.0014, 8000, 20000.0, 20010.0, 7000, 1000)},
        ),
        _cycle(
            3,
            "В",
            "2026-08-30T11:40:00+00:00",
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
    r = build_metrics_journal(cycles, batch_gap_seconds=GAP)
    assert r["total_batches"] == 2
    # Журнал — последние батчи первыми: сначала одиночный «В», затем «А+Б».
    assert r["batches"][0]["count"] == 1
    assert r["batches"][0]["cost_scoring"] == 0.0008
    assert r["batches"][0]["cost_analysis"] == 0.001
    assert r["batches"][0]["cost_total"] == 0.0018
    assert r["batches"][1]["count"] == 2
    # Сумма стоимостей по батчу, среднее остальных метрик.
    assert r["batches"][1]["cost_scoring"] == round(0.0026 + 0.0014, 8)
    assert r["batches"][1]["cost_total"] == round(0.0026 + 0.0014, 8)
    assert r["batches"][1]["tokens_scoring"] == round((10983 + 8000) / 2, 3)
    assert r["batches"][1]["duration_ms"] == round((31525.0 + 20010.0) / 2, 3)
    assert r["batches"][1]["llm"]["tokens"] == round((9983 + 7000) / 2, 3)
    # Средние/разброс — по отдельным циклам скоринга (не по батчам).
    s = r["scoring_stats"]
    assert s["count"] == 3
    assert s["cost"]["avg"] == round((0.0026 + 0.0014 + 0.0008) / 3, 6)
    assert s["llm"]["tokens"]["count"] == 3
    # Журнал по датам: суммирование токенов/стоимости.
    by = {d["date"]: d for d in r["by_date"]}
    assert by["2026-08-30"]["scoring_tokens"] == 10983 + 8000 + 4000
    assert by["2026-08-30"]["analysis_tokens"] == 3000


def test_batch_gap_controls_batching() -> None:
    cycles = [
        _cycle(
            1,
            "А",
            "2026-08-30T10:00:00+00:00",
            {"scoring": _scoring(0.001, 100, 10.0, 12.0, 90, 10)},
        ),
        _cycle(
            2,
            "Б",
            "2026-08-30T10:30:00+00:00",
            {"scoring": _scoring(0.002, 200, 20.0, 22.0, 190, 10)},
        ),
    ]
    # Зазор 1800 с: при пороге 3600 — один батч, при пороге 600 — два.
    assert build_metrics_journal(cycles, batch_gap_seconds=3600.0)["total_batches"] == 1
    assert build_metrics_journal(cycles, batch_gap_seconds=600.0)["total_batches"] == 2


def test_journal_without_components_uses_stage_totals() -> None:
    # Старые данные без `components`: метрики компонентов отсутствуют, но
    # стоимость/токены/время станции считаются на уровне стадии.
    cycles = [
        _cycle(
            1,
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
    r = build_metrics_journal(cycles, batch_gap_seconds=GAP)
    batch = r["batches"][0]
    assert batch["llm"] is None and batch["embeddings"] is None
    assert batch["tokens_scoring"] == 9983.0
    assert r["scoring_stats"]["count"] == 1
    assert r["scoring_stats"]["tokens"]["avg"] == 9983.0
    assert r["scoring_stats"]["llm"]["tokens"]["count"] == 0


def test_journal_empty() -> None:
    r = build_metrics_journal([])
    assert r["total_batches"] == 0
    assert r["batches"] == []
    assert r["scoring_stats"]["count"] == 0
    assert r["by_date"] == []


def test_journal_skips_empty_costs_and_limits_batches() -> None:
    cycles = [
        _cycle(1, "А", "2026-08-30T10:00:00+00:00", {}),  # без стоимости — пропускается
        _cycle(
            2,
            "Б",
            "2026-08-30T10:30:00+00:00",
            {"scoring": _scoring(0.001, 100, 10.0, 12.0, 90, 10)},
        ),
    ]
    r = build_metrics_journal(cycles, limit=1, batch_gap_seconds=GAP)
    assert r["total_batches"] == 1
    assert len(r["batches"]) == 1


def test_journal_groups_by_iteration() -> None:
    # Закупки с одинаковым номером итерации попадают в один батч (даже при малом
    # зазоре по времени); разные итерации — в разные батчи. Площадка копируется.
    cycles = [
        {
            "evaluation_id": 1,
            "procurement_id": 1,
            "number": "А",
            "subject": "Предмет",
            "platform": "zakupki_gov_44fz",
            "iteration": 3,
            "created_at": "2026-08-30T10:00:00+00:00",
            "costs": {"scoring": _scoring(0.001, 100, 10.0, 12.0, 90, 10)},
        },
        {
            "evaluation_id": 2,
            "procurement_id": 2,
            "number": "Б",
            "subject": "Предмет",
            "platform": "zakupki_gov_44fz",
            "iteration": 3,
            "created_at": "2026-08-30T10:01:00+00:00",  # 60 с — внутри паузы
            "costs": {"scoring": _scoring(0.002, 200, 20.0, 22.0, 190, 10)},
        },
        {
            "evaluation_id": 3,
            "procurement_id": 3,
            "number": "В",
            "subject": "Предмет",
            "platform": "roseltorg_44fz",
            "iteration": 4,
            "created_at": "2026-08-30T10:02:00+00:00",
            "costs": {"scoring": _scoring(0.003, 300, 30.0, 32.0, 290, 10)},
        },
    ]
    r = build_metrics_journal(cycles, batch_gap_seconds=GAP)
    assert r["total_batches"] == 2
    # Последний батч первым — итерация 4, затем итерация 3 (2 закупки).
    assert r["batches"][0]["iteration"] == 4
    assert r["batches"][0]["platform"] == "roseltorg_44fz"
    assert r["batches"][0]["count"] == 1
    assert r["batches"][1]["iteration"] == 3
    assert r["batches"][1]["platform"] == "zakupki_gov_44fz"
    assert r["batches"][1]["count"] == 2
    assert r["batches"][1]["cost_scoring"] == round(0.001 + 0.002, 8)
    # Средние по батчу итерации 3.
    assert r["batches"][1]["tokens_scoring"] == round((100 + 200) / 2, 3)
