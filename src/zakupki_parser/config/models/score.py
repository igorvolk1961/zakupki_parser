"""Модель конфигурации скоринга (config_score.yaml)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCORE_METHOD_DEFAULT = "default"
SCORE_METHOD_EXTERNAL = "external"
SCORE_METHOD_CALCULATING = "calculating"
SCORE_METHOD_DEADLINE_EXPIRED = "deadline_expired"


class ScoreConfig(BaseModel):
    """Конфигурация скоринга закупок.

    ``method``:
      - ``default`` — внутренняя эвристика Fit × P(win) × Margin
        (Margin = НМЦК, P(win) из ``p_win``, Fit из таблицы ``fit_table`` по ОКПД2);
      - ``external`` — расчёт внешним сервисом (на вход — все характеристики закупки).

    ``external_call_mode`` (для method=external):
      - ``before_save`` — вызов перед записью закупки в БД;
      - ``worker`` — отдельным воркером по записям со score_method=default
        (перед вызовом ставится score_method=calculating, чтобы не вызывать повторно).
    """

    method: Literal["default", "external"] = Field(default="default")
    external_service_url: str | None = Field(default=None)
    external_call_mode: Literal["before_save", "worker"] = Field(default="before_save")
    fit_table: dict[str, float] = Field(
        default_factory=dict, description="таблица fit(ОКПД2): код -> коэффициент"
    )
    default_fit: float = Field(
        default=0.5, ge=0, le=1, description="fit для кода, отсутствующего в fit_table"
    )
    p_win: float = Field(default=1.0, ge=0, le=1, description="вероятность победы P(win)")
    external_timeout_seconds: float = Field(default=30.0, ge=0)
