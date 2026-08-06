"""Загрузка и сохранение тестового датасета (JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zakupki_mos_simulator.data.models import (
    BALANCE_TOLERANCE_PERCENT,
    CATEGORIES,
    Dataset,
)


def dataset_path(package_data: str = "data/dataset.json") -> Path:
    """Путь к датасету по умолчанию относительно подпроекта."""
    return Path(__file__).resolve().parents[1] / package_data


def load_dataset(path: str | Path | None = None) -> Dataset:
    """Читает датасет из JSON-файла (пустой, если файла нет)."""
    p = Path(path) if path else dataset_path()
    if not p.exists():
        return Dataset()
    with p.open("r", encoding="utf-8") as fh:
        return Dataset.model_validate(json.load(fh))


def save_dataset(dataset: Dataset, path: str | Path | None = None) -> Path:
    """Записывает датасет в JSON-файл (pretty-printed, ensure_ascii=False)."""
    p = Path(path) if path else dataset_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(
            json.loads(dataset.model_dump_json()),
            fh,
            ensure_ascii=False,
            indent=2,
        )
    return p


def balance_report(dataset: Dataset) -> dict[str, Any]:
    """Отчёт по долям категорий и превышению допуска балансировки."""
    counts = dataset.category_counts()
    total = len(dataset.procurements)
    report: dict[str, Any] = {"total": total, "counts": counts, "violations": []}
    if total == 0:
        return report
    for cat in CATEGORIES:
        share = counts[cat] / total * 100.0
        ideal = 100.0 / len(CATEGORIES)
        report[f"{cat}_percent"] = round(share, 1)
        if abs(share - ideal) > BALANCE_TOLERANCE_PERCENT:
            report["violations"].append(
                f"{cat}: {share:.1f}% (ожидается ~{ideal:.0f}%, "
                f"допуск {BALANCE_TOLERANCE_PERCENT:.0f}%)"
            )
    return report
