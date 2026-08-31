"""Калибровка `sim_limit` (= `embedding_filter_threshold`) под «сохранить хороших».

Критерий: подбирается максимальный порог по косинусному сходству, при котором
доля сохранённых «хороших» (fit >= fit_threshold) не ниже цели (90% / 95% / 100%).
Результаты пишутся в `data/out/`.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
from typing import Any

import asyncpg
import numpy as np

DSN = os.environ.get(
    "ZAKUPKI_DB_DSN", "postgresql://postgres:postgres@localhost:5432/zakupki"
).replace("postgresql+asyncpg://", "postgresql://")
OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "out"
)


async def load() -> tuple[np.ndarray, np.ndarray]:
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        "SELECT procurement_id, fit_score AS fit, embedding_similarity AS sim "
        "FROM procurement_evaluations "
        "WHERE embedding_similarity IS NOT NULL AND score_method = 'fit'"
    )
    await conn.close()
    per: dict[int, tuple[float, float]] = {}
    for r in rows:
        per.setdefault(r["procurement_id"], (float(r["fit"]), float(r["sim"])))
    fit = np.array([v[0] for v in per.values()], dtype=float)
    sim = np.array([v[1] for v in per.values()], dtype=float)
    return fit, sim


def keep_good_table(
    fit: np.ndarray, sim: np.ndarray, goals: tuple[float, ...]
) -> list[dict[str, Any]]:
    fit_values = sorted(set(np.round(fit, 4).tolist()))
    grid = sorted({round(v, 3) for v in fit_values} | {0.30, 0.70, 0.90})
    rows: list[dict[str, Any]] = []
    for T in grid:
        good = fit >= T
        n_good = int(good.sum())
        n_bad = int((~good).sum())
        if n_good < 15:
            continue
        gs = sim[good]
        bs = sim[~good]
        base = [np.percentile(gs, 5, method=m) for m in ("lower", "linear", "higher")]
        for goal in goals:
            if goal >= 1.0:
                cands = [float(gs.min())]
            else:
                cands = sorted({float(x) for x in base}, reverse=True)
            pick: float | None = None
            for c in cands:
                if (gs >= c).mean() >= goal:
                    pick = c
                    break
            if pick is None:
                pick = float(gs.min())
            rows.append(
                {
                    "fit_threshold": round(T, 3),
                    "keep_goal": round(goal, 2),
                    "n_good": n_good,
                    "n_bad": n_bad,
                    "sim_limit": round(pick, 4),
                    "keep_good_actual": round(float((gs >= pick).mean()), 4),
                    "reject_bad": round(float(np.mean(bs < pick)), 4) if n_bad else float("nan"),
                    "overall_reject": round(float(np.mean(sim < pick)), 4),
                }
            )
    return rows


def fmt(p: Any) -> str:
    if p is None or (isinstance(p, float) and p != p):
        return "—"
    return f"{p * 100:.1f}%"


async def main() -> None:
    fit, sim = await load()
    corr = float(np.corrcoef(fit, sim)[0, 1])
    n = len(fit)
    rows = keep_good_table(fit, sim, (0.90, 0.95, 1.0))
    keys = [
        "fit_threshold",
        "keep_goal",
        "n_good",
        "n_bad",
        "sim_limit",
        "keep_good_actual",
        "reject_bad",
        "overall_reject",
    ]
    with open(
        os.path.join(OUT_DIR, "scoring_embedding_keep_good.csv"), "w", newline="", encoding="utf-8"
    ) as f:
        w = csv.DictWriter(f, fieldnames=keys, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    L: list[str] = []
    L.append("# Калибровка `sim_limit` так, чтобы СОХРАНИТЬ ≥90% / 95% / 100% хороших закупок")
    L.append("")
    L.append(
        f"- Выборка: **{n} уникальных закупок**, `score_method='fit'`, дедуп по `procurement_id`."
    )
    L.append(
        "- `fit_score` нормирован 0..1 (= `fit_multiplier`), `embedding_similarity` — "
        "косинусное сходство 0..1."
    )
    L.append(
        f"- Корреляция sim↔fit = **{corr:.2f}**; диапазон sim = {sim.min():.3f}–{sim.max():.3f}."
    )
    L.append("")
    L.append("## Критерий")
    L.append("")
    L.append(
        "«Хорошие» = закупки с `fit ≥ fit_threshold`. Фильтр отсекает "
        "`embedding_similarity < sim_limit`."
    )
    L.append("Подбирается МАКСИМАЛЬНЫЙ `sim_limit`, при котором доля сохранённых «хороших» ≥ цели")
    L.append("(**90%**, **95%**, **100%**). Чем выше `sim_limit`, тем больше отсекается и «плохих»")
    L.append("(`fit < fit_threshold`) — это и есть выигрыш по экономии LLM.")
    L.append("")
    L.append("## `sim_limit` при сохранении 90% / 95% / 100% хороших")
    L.append("")
    L.append(
        "| fit_threshold | цель | **sim_limit** | сохранено хороших (факт) | отсечено плохих | "
        "всего отсеется |"
    )
    L.append("|:---:|---:|---:|---:|---:|---:|")
    for r in rows:
        L.append(
            f"| {r['fit_threshold']:.2f} | {r['keep_goal'] * 100:.0f}% | **{r['sim_limit']:.4f}** "
            f"| {fmt(r['keep_good_actual'])} | "
            f"{fmt(r['reject_bad'])} | {fmt(r['overall_reject'])} |"
        )
    L.append("")
    L.append("## Ориентиры")
    L.append("")
    L.append("- **fit_threshold = 0.3** (мин. релевантность из BR-02):")
    for r in rows:
        if r["fit_threshold"] == 0.3:
            L.append(
                f"  - сохранить **{r['keep_goal'] * 100:.0f}%** хороших → "
                f"`sim_limit ≈ {r['sim_limit']:.4f}`; "
                f"отсечёт {fmt(r['reject_bad'])} плохих, {fmt(r['overall_reject'])} всех."
            )
    L.append("- **fit_threshold = 0.7** (`notify_min_fit_score`):")
    for r in rows:
        if r["fit_threshold"] == 0.7:
            L.append(
                f"  - сохранить **{r['keep_goal'] * 100:.0f}%** хороших → "
                f"`sim_limit ≈ {r['sim_limit']:.4f}`; "
                f"отсечёт {fmt(r['reject_bad'])} плохих, {fmt(r['overall_reject'])} всех."
            )
    L.append("")
    L.append("## Как применить")
    L.append("")
    L.append(
        "1. Выберите цель по сохранению хороших (рекомендуется ≥90%, т.е. `sim_limit` "
        "по строке 90%)."
    )
    L.append("2. Пропишите `scoring.embedding_filter_threshold` в `configs/config_service.yaml`")
    L.append(
        "   (runtime через `/api/config/scoring`) или фоллбэк `src/scoring_service/config.yaml`."
    )
    L.append("3. Перекалибруйте при изменении текста профиля для эмбеддинга.")
    L.append("")
    with open(os.path.join(OUT_DIR, "scoring_embedding_keep_good.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    pts: dict[float, dict[float, float]] = {}
    for r in rows:
        pts.setdefault(r["fit_threshold"], {})[r["keep_goal"]] = r["sim_limit"]
    with open(
        os.path.join(OUT_DIR, "scoring_embedding_keep_good.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(
            {"corr": round(corr, 3), "n": n, "points": {str(k): v for k, v in pts.items()}},
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"sim_min={sim.min():.4f} sim_max={sim.max():.4f} corr={corr:.2f}")
    for r in rows:
        print(r)


asyncio.run(main())
