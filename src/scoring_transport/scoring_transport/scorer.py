"""Дефолтный score (приоритет) закупки — зеркало эвристики парсера.

Приоритет очереди = дефолтный score: Fit(ОКПД2) × P(win) × Margin (Margin = НМЦК).
Если парсер уже сохранил дефолтный score в карточке (score_method=default) — берём его,
иначе пересчитываем.
"""

from __future__ import annotations

import re
from typing import Any

from scoring_transport.settings import Settings

SCORE_METHOD_DEFAULT = "default"


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def _okpd2_code(record: dict[str, Any]) -> str:
    value = record.get("okpd2_codes") or record.get("okpd2_code") or ""
    return str(value).split(",")[0].strip()


def _fit_for_code(record: dict[str, Any], settings: Settings) -> float:
    code = _okpd2_code(record)
    digits = _digits(code)
    if not digits:
        return settings.default_fit
    best_len = 0
    best_fit: float | None = None
    for key, value in settings.fit_table.items():
        key_digits = _digits(key)
        if key_digits and digits.startswith(key_digits) and len(key_digits) > best_len:
            best_len = len(key_digits)
            best_fit = value
    return best_fit if best_fit is not None else settings.default_fit


def compute_default_score(record: dict[str, Any], settings: Settings) -> float:
    """Дефолтный score из карточки: Fit × P(win) × НМЦК (округление до 2 знаков).

    ВНИМАНИЕ: это зеркало эвристики парсера (src/zakupki_parser/scoring.py) для
    расчёта приоритета очереди. Если парсер изменит расчёт Fit/OKPD2, обновить и здесь,
    либо передавать приоритет явно в ingest (поле ``priority``).
    """
    fit = _fit_for_code(record, settings)
    margin = float(record.get("nmck") or 0.0)
    return round(fit * settings.p_win * margin, 2)


def priority_for(record: dict[str, Any], settings: Settings) -> float:
    """Приоритет задачи: сохранённый дефолтный score либо пересчёт."""
    if record.get("score_method") == SCORE_METHOD_DEFAULT and record.get("score") is not None:
        try:
            return float(record["score"])
        except (TypeError, ValueError):
            pass
    return compute_default_score(record, settings)
