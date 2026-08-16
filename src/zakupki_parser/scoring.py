"""Скоринг закупок: внутренняя эвристика (дефолтный score).

Формула: Score = Fit(ОКПД2) × P(win) × Margin.
Простейшая эвристика: Margin = НМЦК × margin_rate, P(win) = 1, Fit — таблица из config_score.yaml.
Финальный внешний score приходит асинхронно через POST /api/procurements/{id}/score
(конвейер transport + scoring_service + Redis, ADR-7).
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
    ScoreConfig,
)

logger = logging.getLogger(__name__)


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def _okpd2_code(record: dict[str, Any]) -> str:
    """Первый код ОКПД2 из записи (нормализованный)."""
    value = record.get("okpd2_codes") or record.get("okpd2_code") or ""
    return str(value).split(",")[0].strip()


def _fit_for_code(
    code: str,
    fit_table: dict[str, float],
    default_fit: float,
    empty_code_fit: float,
) -> float:
    """Fit по ОКПД2: пустой код — ``empty_code_fit``; иначе точный код или
    ближайший предок (префикс) из таблицы; при отсутствии — ``default_fit``."""
    digits = _digits(code)
    if not digits:
        return empty_code_fit
    best_len = 0
    best_fit: float | None = None
    for key, value in fit_table.items():
        key_digits = _digits(key)
        if key_digits and digits.startswith(key_digits) and len(key_digits) > best_len:
            best_len = len(key_digits)
            best_fit = value
    return best_fit if best_fit is not None else default_fit


def compute_default_fit(record: dict[str, Any], cfg: ScoreConfig) -> float:
    """Множитель Fit (0..1) по ОКПД2 из config_score.yaml."""
    return _fit_for_code(_okpd2_code(record), cfg.fit_table, cfg.default_fit, cfg.empty_code_fit)


def compute_default_score(record: dict[str, Any], cfg: ScoreConfig) -> float:
    """Внутренняя эвристика: Fit × P(win) × Margin (Margin = НМЦК × margin_rate)."""
    fit = compute_default_fit(record, cfg)
    margin = float(record.get("nmck") or 0.0) * cfg.margin_rate
    return round(fit * cfg.p_win * margin, 2)


async def score_for_record(
    record: dict[str, Any],
    cfg: ScoreConfig,
    now: datetime | None = None,
    *,
    active_only: bool = True,
) -> tuple[float, float, str]:
    """Возвращает (score, fit_score, score_method) для записи перед сохранением.

    - при поиске по ВСЕМ закупкам (``active_only=False``) просроченные не метим
      ``deadline_expired``: метод всегда ``default``, чтобы они доезжали до внешнего
      скоринга и уведомления;
    - иначе — просроченный срок подачи заявок (deadline < now) → score=0,
      score_method=deadline_expired;
    - в остальных случаях — внутренняя эвристика (score_method=default). Финальный
      внешний score проставит конвейер скоринга через POST /score (ADR-7).
    """
    if active_only:
        deadline = record.get("deadline")
        if isinstance(deadline, datetime) and now is not None and deadline < now:
            return 0.0, compute_default_fit(record, cfg), SCORE_METHOD_DEADLINE_EXPIRED
    fit = compute_default_fit(record, cfg)
    return compute_default_score(record, cfg), fit, SCORE_METHOD_DEFAULT


class ScoringTransportClient:
    """Клиент transport-конвейера скоринга (авто-пуш задания после сохранения, ADR-7).

    Вызов best-effort: при недоступности транспорта задание не ставится, но «сырая»
    закупка уже сохранена в БД с дефолтным скором (вежливая деградация).
    """

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self._base = url.rstrip("/")
        self._timeout = timeout

    async def enqueue(
        self,
        procurement_id: int,
        priority: float,
        transport: httpx.AsyncBaseTransport | None = None,
        stage: str = "fit",
    ) -> None:
        """Поставить задание на скоринг: POST /api/scoring/jobs.

        ``stage`` — стадия каскада (fit/pwin/margin); транспорт направляет задание
        в соответствующую Redis-очередь.
        """
        url = f"{self._base}/api/scoring/jobs"
        payload = {"procurement_id": procurement_id, "priority": priority, "stage": stage}
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
