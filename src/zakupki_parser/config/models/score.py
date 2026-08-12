"""Модель конфигурации скоринга (config_score.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field

SCORE_METHOD_DEFAULT = "default"
SCORE_METHOD_EXTERNAL = "external"
SCORE_METHOD_DEADLINE_EXPIRED = "deadline_expired"


class ScoreConfig(BaseModel):
    """Конфигурация скоринга закупок.

    Дефолтный score: Fit × P(win) × Margin (Margin = НМЦК, P(win) из ``p_win``,
    Fit из таблицы ``fit_table`` по ОКПД2). Финальный внешний score приходит
    асинхронно через конвейер transport + scoring_service + Redis (ADR-7).
    """

    fit_table: dict[str, float] = Field(
        default_factory=dict, description="таблица fit(ОКПД2): код -> коэффициент"
    )
    default_fit: float = Field(
        default=0.5, ge=0, le=1, description="fit для кода, отсутствующего в fit_table"
    )
    p_win: float = Field(default=1.0, ge=0, le=1, description="вероятность победы P(win)")
    margin_rate: float = Field(
        default=1.0,
        ge=0,
        description="норма прибыли: Margin = НМЦК × margin_rate",
    )
    scoring_transport_url: str | None = Field(
        default=None,
        description="адрес scoring_transport для автопуша задания на внешний скоринг (ADR-7)",
    )
