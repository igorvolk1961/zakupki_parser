"""Скоринг закупок: внутренняя эвристика и клиент внешнего сервиса.

Формула: Score = Fit(ОКПД2) × P(win) × Margin.
Простейшая эвристика: Margin = НМЦК, P(win) = 1, Fit — таблица из config_score.yaml.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx

from zakupki_parser.config.models import (
    SCORE_METHOD_DEADLINE_EXPIRED,
    SCORE_METHOD_DEFAULT,
    SCORE_METHOD_EXTERNAL,
    ScoreConfig,
)
from zakupki_parser.parser.json_utils import json_safe

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


class ExternalScoreClient:
    """Вызывает внешний сервис скоринга (POST всех характеристик закупки)."""

    def __init__(self, cfg: ScoreConfig) -> None:
        self._url = cfg.external_service_url
        self._timeout = cfg.external_timeout_seconds

    async def score(self, record: dict[str, Any]) -> float:
        if not self._url:
            raise ValueError("external_service_url не задан в config_score.yaml")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=json_safe(record))
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, dict):
            if "score" not in data:
                raise ValueError(f"Внешний сервис вернул данные без 'score': {data!r}")
            return float(data["score"])
        return float(data)


async def score_for_record(
    record: dict[str, Any],
    cfg: ScoreConfig,
    external: ExternalScoreClient | None,
    now: datetime | None = None,
) -> tuple[float, str]:
    """Возвращает (score, score_method) для записи перед сохранением.

    - просроченный срок подачи заявок (deadline < now) → score=0,
      score_method=deadline_expired;
    - method=default → внутренняя эвристика (score_method=default);
    - method=external + before_save → вызов внешнего сервиса (external),
      при ошибке — fallback на внутреннюю эвристику (default);
    - method=external + worker → внутренняя эвристика как начальное значение
      (default), финальный внешний score проставит воркер.
    """
    deadline = record.get("deadline")
    if isinstance(deadline, datetime) and now is not None and deadline < now:
        return 0.0, SCORE_METHOD_DEADLINE_EXPIRED

    if cfg.method == SCORE_METHOD_EXTERNAL and cfg.external_call_mode == "before_save":
        if external is None:
            external = ExternalScoreClient(cfg)
        try:
            value = await external.score(record)
            return round(value, 2), SCORE_METHOD_EXTERNAL
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка внешнего скоринга, fallback на default: %s", exc)
            return compute_default_score(record, cfg), SCORE_METHOD_DEFAULT
    # default, либо external+worker (начальное значение default)
    return compute_default_score(record, cfg), SCORE_METHOD_DEFAULT
