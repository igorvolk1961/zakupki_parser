"""Скоринг закупок: внутренняя эвристика (дефолтный score).

Формула: Score = Fit(ОКПД2) × P(win) × Margin.
Простейшая эвристика: Margin = НМЦК, P(win) = 1, Fit — таблица из config_score.yaml.
Финальный внешний score приходит асинхронно через POST /api/procurements/{id}/score
(конвейер transport + scoring_service + Redis, ADR-7).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from zakupki_parser.config.models import (
    SCORE_METHOD_DEADLINE_EXPIRED,
    SCORE_METHOD_DEFAULT,
    ScoreConfig,
)

logger = logging.getLogger(__name__)


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def _okpd2_code(record: dict[str, Any]) -> str:
    """Первый код ОКПД2 из записи (нормализованный)."""
    value = record.get("okpd2_codes") or record.get("okpd2_code") or ""
    return str(value).split(",")[0].strip()


def _fit_for_code(code: str, fit_table: dict[str, float], default_fit: float) -> float:
    """Fit по ОКПД2: точный код, иначе ближайший предок (префикс) из таблицы."""
    digits = _digits(code)
    if not digits:
        return default_fit
    best_len = 0
    best_fit: float | None = None
    for key, value in fit_table.items():
        key_digits = _digits(key)
        if key_digits and digits.startswith(key_digits) and len(key_digits) > best_len:
            best_len = len(key_digits)
            best_fit = value
    return best_fit if best_fit is not None else default_fit


def compute_default_score(record: dict[str, Any], cfg: ScoreConfig) -> float:
    """Внутренняя эвристика: Fit × P(win) × Margin (Margin = НМЦК)."""
    fit = _fit_for_code(_okpd2_code(record), cfg.fit_table, cfg.default_fit)
    margin = float(record.get("nmck") or 0.0)
    return round(fit * cfg.p_win * margin, 2)


async def score_for_record(
    record: dict[str, Any],
    cfg: ScoreConfig,
    now: datetime | None = None,
) -> tuple[float, str]:
    """Возвращает (score, score_method) для записи перед сохранением.

    - просроченный срок подачи заявок (deadline < now) → score=0,
      score_method=deadline_expired;
    - иначе — внутренняя эвристика (score_method=default). Финальный внешний score
      проставит конвейер скоринга через POST /score (ADR-7).
    """
    deadline = record.get("deadline")
    if isinstance(deadline, datetime) and now is not None and deadline < now:
        return 0.0, SCORE_METHOD_DEADLINE_EXPIRED
    return compute_default_score(record, cfg), SCORE_METHOD_DEFAULT
