"""Модель конфигурации скоринга (config_score.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field

SCORE_METHOD_DEFAULT = "default"
SCORE_METHOD_FIT = "fit"
SCORE_METHOD_PWIN = "pwin"
SCORE_METHOD_MARGIN = "margin"
SCORE_METHOD_DEADLINE_EXPIRED = "deadline_expired"

# Стадии внешнего каскада скоринга (Fit -> P(win) -> Margin). Значение
# score_method записи, прошедшей внешний скоринг, — одна из этих стадий.
SCORE_METHOD_STAGES = (SCORE_METHOD_FIT, SCORE_METHOD_PWIN, SCORE_METHOD_MARGIN)


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
    empty_code_fit: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="fit для закупки без кода ОКПД2 (пустой код)",
    )
    p_win: float = Field(default=1.0, ge=0, le=1, description="вероятность победы P(win)")
    margin_rate: float = Field(
        default=1.0,
        ge=0,
        description="норма прибыли: Margin = НМЦК × margin_rate",
    )
    pwin_fit_threshold: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description=(
            "порог запуска стадии P(win): закупка ставится в очередь pwin:jobs, "
            "если внешний fit_score >= порога (каскад Fit -> P(win) -> Margin)"
        ),
    )
    margin_threshold: float = Field(
        default=0.6,
        ge=0,
        description=(
            "порог запуска стадии Margin: закупка ставится в очередь margin:jobs, "
            "если накопленный score (fit_score × p_win) >= порога"
        ),
    )
    pwin_enabled: bool = Field(
        default=False,
        description=(
            "включена ли стадия P(win): парсер ставит задачи в очередь pwin:jobs "
            "только при True. Держать False, пока сервис pwin_service не развёрнут "
            "(защита от зависания задач при ролл-ауте)"
        ),
    )
    margin_enabled: bool = Field(
        default=False,
        description=(
            "включена ли стадия Margin: парсер ставит задачи в очередь margin:jobs "
            "только при True. Держать False, пока сервис margin_service не развёрнут"
        ),
    )
    scoring_transport_url: str | None = Field(
        default=None,
        description="адрес scoring_transport для автопуша задания на внешний скоринг (ADR-7)",
    )
