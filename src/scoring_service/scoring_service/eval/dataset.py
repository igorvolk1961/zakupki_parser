"""Загрузка тестового набора данных: пары (описание закупки — скор).

Формат JSON: список объектов ``{"description": str, "expected_fit": float}``.
Допустим также CSV-столбец ``expected_fit``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class EvalItem(BaseModel):
    """Один элемент тестового набора."""

    description: str
    expected_fit: float = Field(description="Ожидаемая Fit-оценка 0..10")

    @field_validator("expected_fit")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(10.0, value))


def load_dataset(path: Path) -> list[EvalItem]:
    """Загрузить датасет из JSON- или CSV-файла."""
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [EvalItem.model_validate(item) for item in raw]

    items: list[EvalItem] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            items.append(
                EvalItem(
                    description=row["description"],
                    expected_fit=float(row["expected_fit"]),
                )
            )
    return items
