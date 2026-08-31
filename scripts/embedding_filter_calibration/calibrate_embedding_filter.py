"""Калибровка `embedding_filter_threshold` (sim_limit) по `fit_threshold`.

Первая постановка: подбирается порог по косинусному сходству, отсекающий
>=90% закупок с низким Fit. Результаты пишутся в `data/out/`.
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


def calibrate(fit: np.ndarray, sim: np.ndarray) -> list[dict[str, Any]]:
    fit_values = sorted(set(np.round(fit, 4).tolist()))
    grid = sorted({round(v, 3) for v in fit_values} | {0.10, 0.30, 0.50, 0.70, 0.90})
    rows: list[dict[str, Any]] = []
    for T in grid:
        bad = fit < T
        n_bad = int(bad.sum())
        if n_bad < 15:
            rows.append(
                {
                    "fit_threshold": round(T, 3),
                    "n_lowfit": n_bad,
                    "sim_limit": None,
                    "recall_bad": None,
                    "false_reject_good": None,
                    "n_good": int((~bad).sum()),
                    "overall_reject": None,
                    "note": "мало данных (n<15)",
                }
            )
            continue
        bs = sim[bad]
        s_lin = np.percentile(bs, 90, method="linear")
        s_lo = np.percentile(bs, 90, method="lower")
        s_hi = np.percentile(bs, 90, method="higher")
        s_pick: float | None = None
        for cand in sorted({s_lo, s_lin, s_hi}):
            if np.mean(bs < cand) >= 0.9:
                s_pick = cand
                break
        if s_pick is None:
            s_pick = float(s_hi)
        recall = float(np.mean(sim[bad] < s_pick))
        good = sim[~bad]
        fp = float(np.mean(good < s_pick)) if len(good) else float("nan")
        overall = float(np.mean(sim < s_pick))
        rows.append(
            {
                "fit_threshold": round(T, 3),
                "n_lowfit": n_bad,
                "sim_limit": round(float(s_pick), 4),
                "recall_bad": round(recall, 4),
                "false_reject_good": round(fp, 4),
                "n_good": int(len(good)),
                "overall_reject": round(overall, 4),
                "note": f"q90: lin={s_lin:.4f} lo={s_lo:.4f} hi={s_hi:.4f}",
            }
        )
    return rows


def sweep(fit: np.ndarray, sim: np.ndarray, T: float, steps: int = 25) -> list[dict[str, Any]]:
    lo, hi = np.percentile(sim, 5), np.percentile(sim, 95)
    thr = np.linspace(lo, hi, steps)
    bad = fit < T
    good = ~bad
    rows: list[dict[str, Any]] = []
    for s in thr:
        rows.append(
            {
                "sim_limit": round(float(s), 4),
                "recall_bad": round(float(np.mean(sim[bad] < s)), 4),
                "false_reject_good": round(float(np.mean(sim[good] < s)), 4),
                "overall_reject": round(float(np.mean(sim < s)), 4),
                "n_lowfit_kept": int((sim[bad] >= s).sum()),
            }
        )
    return rows


def write_csv(rows: list[dict[str, Any]], path: str, keys: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})


async def main() -> None:
    fit, sim = await load()
    corr = float(np.corrcoef(fit, sim)[0, 1])
    n = len(fit)
    dep = calibrate(fit, sim)

    os.makedirs(OUT_DIR, exist_ok=True)
    dep_keys = [
        "fit_threshold",
        "n_lowfit",
        "sim_limit",
        "recall_bad",
        "false_reject_good",
        "n_good",
        "overall_reject",
        "note",
    ]
    write_csv(dep, os.path.join(OUT_DIR, "scoring_embedding_calibration.csv"), dep_keys)

    sweeps: dict[float, list[dict[str, Any]]] = {}
    for T in (0.3, 0.7):
        sw = sweep(fit, sim, T)
        sweeps[T] = sw
        write_csv(
            sw,
            os.path.join(OUT_DIR, f"scoring_embedding_sweep_fit{T}.csv"),
            ["sim_limit", "recall_bad", "false_reject_good", "overall_reject", "n_lowfit_kept"],
        )

    L: list[str] = []
    L.append(
        "# Калибровка порога по косинусному сходству (`sim_limit` = `embedding_filter_threshold`)"
    )
    L.append("")
    L.append("- Источник: `procurement_evaluations` (per-profile результат скоринга).")
    L.append(
        "- Выборка: `score_method='fit'` c непустым `embedding_similarity` и `fit_score`, "
        "дедупликация по `procurement_id`,"
    )
    L.append(
        "  т.к. все активные профили (`bbk-it` 5/13/15/17) имеют один `comp_hash` — "
        "одинаковый текст эмбеддинга."
    )
    L.append(f"- **Объём: {n} уникальных закупок; корреляция sim↔fit = {corr:.2f}.**")
    L.append("- `fit_score` нормирован 0..1 (= `fit_multiplier` = final_fit/max_fit, max_fit=10);")
    L.append("  `embedding_similarity` — косинусное сходство профиль↔описание, 0..1.")
    L.append(
        "- `min_fit_threshold` в профилях **не задан (NULL)** — применяется дефолт/"
        "`notify_min_fit_score`."
    )
    L.append(
        "  Точки-ориентиры: **0.3** (пример из BR-02 «релевантность») и **0.7** "
        "(`notify_min_fit_score`)."
    )
    L.append("")
    L.append("## Метод")
    L.append("")
    L.append(
        "Для каждого `fit_threshold = T` берётся подвыборка **нерелевантных** закупок (`fit < T`),"
    )
    L.append(
        "выбирается `sim_limit` = минимальный порог `embedding_similarity`, при котором "
        "отсекается **≥ 90%**"
    )
    L.append(
        "этой подвыборки (перебор 90-х перцентилей: lower/linear/higher). При включении фильтра"
    )
    L.append(
        "`embedding_similarity < embedding_filter_threshold` LLM для таких закупок не "
        "запускается (`fit=0`)."
    )
    L.append("")
    L.append("## Зависимость `embedding_filter_threshold` от `fit_threshold` (искомый ответ)")
    L.append("")
    L.append(
        "| fit_threshold | n(фит<T) | **sim_limit** | отсекается «плохих» | "
        "теряется «хороших» | всего отсеется |"
    )
    L.append("|:---:|---:|---:|---:|---:|---:|")
    for r in dep:
        if r["sim_limit"] is None:
            L.append(f"| {r['fit_threshold']:.2f} | {r['n_lowfit']} | — | — | — | — |")
        else:
            L.append(
                f"| {r['fit_threshold']:.2f} | {r['n_lowfit']} | **{r['sim_limit']:.4f}** "
                f"| {r['recall_bad'] * 100:.1f}% | {r['false_reject_good'] * 100:.1f}% "
                f"| {r['overall_reject'] * 100:.1f}% |"
            )
    L.append("")
    L.append("### Ключевой вывод")
    L.append("")
    L.append(
        f"Кривая почти плоская (0.64–0.68) из-за узкого диапазона `embedding_similarity` "
        f"({sim.min():.3f}–{sim.max():.3f}) и корреляции всего {corr:.2f}: **векторная близость "
        "слабо разделяет** "
    )
    L.append(
        "релевантные и нерелевантные закупки. Порог, отсекающий ≥90% «плохих», одновременно "
        "отбрасывает "
    )
    L.append("**~60–67% «хороших»** (`fit ≥ T`) и ~65–78% всех закупок. Это означает:")
    L.append(
        "- включать такой фильтр **не рекомендуется** — он съедает релевантные закупки почти "
        "так же, как нерелевантные;"
    )
    L.append(
        "- в текущем виде ветка близости пригодна только как слабый «сигнал-хинт» "
        "(`giga_embedding_alpha`), а не как жёсткая отсечка;"
    )
    L.append(
        "- целесообразнее резать по стоимости LLM мягче (низкий `sim_limit`), принимая "
        "меньший recall «плохих»."
    )
    L.append("")
    L.append("## Компромисс «recall плохих ↔ потеря хороших»")
    L.append("")
    for T in (0.3, 0.7):
        L.append(f"### fit_threshold = {T} (насколько можно поднять sim_limit, не задушив хорошие)")
        L.append("")
        L.append(
            "| sim_limit | recall_плохих | потеря_хороших | всего_отсеется | плохих_осталось |"
        )
        L.append("|---:|---:|---:|---:|---:|")
        for r in sweeps[T]:
            L.append(
                f"| {r['sim_limit']:.3f} | {r['recall_bad'] * 100:.1f}% | "
                f"{r['false_reject_good'] * 100:.1f}% | {r['overall_reject'] * 100:.1f}% "
                f"| {r['n_lowfit_kept']} |"
            )
        L.append("")
    L.append("## Как применить")
    L.append("")
    L.append(
        "1. Задайте целевой `fit_threshold` (= `min_fit_threshold` профиля либо "
        "`notify_min_fit_score=0.7`)."
    )
    L.append(
        "2. Возьмите `sim_limit` из таблицы выше; при необходимости снизьте его до уровня, где"
    )
    L.append("   `потеря_хороших` приемлема, осознанно пожертвовав `recall_плохих`.")
    L.append(
        "3. Пропишите значение в `configs/config_service.yaml -> "
        "scoring.embedding_filter_threshold`"
    )
    L.append(
        "   (runtime через `/api/config/scoring`) либо в фоллбэк `src/scoring_service/config.yaml`."
    )
    L.append(
        "4. Перекалибруйте после изменения текста профиля для эмбеддинга "
        "(предупреждение в `config.yaml`)."
    )
    L.append("")
    with open(
        os.path.join(OUT_DIR, "scoring_embedding_calibration.md"), "w", encoding="utf-8"
    ) as f:
        f.write("\n".join(L) + "\n")

    pts = [
        {"x": r["fit_threshold"], "y": r["sim_limit"]} for r in dep if r["sim_limit"] is not None
    ]
    with open(
        os.path.join(OUT_DIR, "scoring_embedding_calibration.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(
            {
                "corr": round(corr, 3),
                "n": n,
                "sim_min": round(float(sim.min()), 4),
                "sim_max": round(float(sim.max()), 4),
                "points": pts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("== DEPENDENCY ==")
    for r in dep:
        print(r)
    print("== SWEEP fit<0.3 ==")
    for r in sweeps[0.3]:
        print(r)


asyncio.run(main())
