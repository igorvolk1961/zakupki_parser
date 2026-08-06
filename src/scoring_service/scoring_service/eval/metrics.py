"""Метрики качества оценки Fit на тестовом наборе."""

from __future__ import annotations

import math

from pydantic import BaseModel, Field


class Metrics(BaseModel):
    """Набор метрик сравнения предсказанных и ожидаемых скоров."""

    n: int
    mae: float = Field(description="средняя абсолютная ошибка")
    rmse: float = Field(description="корень из средней квадратичной ошибки")
    accuracy_at_tol: float = Field(description="доля в допуске tol")
    pearson: float | None = Field(description="корреляция Пирсона")
    spearman: float | None = Field(description="корреляция Спирмена")
    tolerance: float = Field(description="допуск для accuracy")


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    if den == 0:
        return None
    return num / den


def _spearman(x: list[float], y: list[float]) -> float | None:
    def _rank(values: list[float]) -> list[float]:
        indexed = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        for pos, idx in enumerate(indexed):
            ranks[idx] = float(pos + 1)
        return ranks

    return _pearson(_rank(x), _rank(y))


def compute_metrics(
    expected: list[float],
    predicted: list[float],
    tolerance: float = 1.0,
) -> Metrics:
    """Рассчитать метрики по спискам ожидаемых и предсказанных скоров."""
    n = len(expected)
    errors = [p - e for p, e in zip(predicted, expected, strict=True)]
    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)
    within = sum(1 for e in errors if abs(e) <= tolerance) / n
    return Metrics(
        n=n,
        mae=round(mae, 4),
        rmse=round(rmse, 4),
        accuracy_at_tol=round(within, 4),
        pearson=_pearson(expected, predicted),
        spearman=_spearman(expected, predicted),
        tolerance=tolerance,
    )
