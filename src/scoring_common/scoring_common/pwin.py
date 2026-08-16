"""Модуль расчёта вероятности победы P(win).

Модель из исследования ``docs/references/Модель P(win) для IT-закупок России...pdf``:

    P(win) = base_pwin × k_smp × k_license × k_large × k_procedure × k_ai

На первом этапе из карточки закупки доступны только ``nmck`` (для ``k_large``) и
``subject``/``okpd2_codes`` (для ``k_ai``). Коэффициенты СМП/лицензий/процедуры
применяются, когда соответствующие поля появятся в карточке (заготовки резолверов),
иначе — 1.0.
"""

from __future__ import annotations

import re
from typing import Any

from scoring_common.config import PwinCoefficients

# Типы процедур для маппинга на коэффициенты. Поле пока не извлекается парсером —
# резолвер по умолчанию возвращает 1.0.
_PROCEDURE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"аукцион", re.IGNORECASE), "auction"),
    (re.compile(r"конкурс", re.IGNORECASE), "contest"),
    (re.compile(r"котиров", re.IGNORECASE), "quotation"),
)


def _nmck(record: dict[str, Any]) -> float:
    try:
        return float(record.get("nmck") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _text(record: dict[str, Any]) -> str:
    parts = []
    for key in ("subject", "okpd2_codes", "okpd2_code"):
        value = record.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _is_ai(record: dict[str, Any], coeffs: PwinCoefficients) -> bool:
    text = _text(record).lower()
    return any(marker in text for marker in coeffs.ai_markers)


def _procedure_kind(record: dict[str, Any]) -> str | None:
    """Тип процедуры из карточки (заготовка: поле не извлекается).

    Пробуем определить по тексту subject/детали. Если тип не распознан — None
    (коэффициент = 1.0).
    """
    text = _text(record)
    if "detail_json" in record and isinstance(record["detail_json"], dict):
        text += " " + str(record["detail_json"].get("procedure_type", ""))
    for pattern, kind in _PROCEDURE_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def _is_smp(record: dict[str, Any]) -> bool:
    """Закупка только для СМП (заготовка: поле ``is_smp_only`` не извлекается)."""
    if "detail_json" in record and isinstance(record["detail_json"], dict):
        value = record["detail_json"].get("is_smp_only")
        if value is not None:
            return bool(value)
    return False


def _license_present(record: dict[str, Any]) -> bool | None:
    """Наличие у компании лицензии ФСТЭК/ФСБ (заготовка: поле не извлекается).

    None — неизвестно: коэффициент не применяется (1.0).
    """
    if "detail_json" in record and isinstance(record["detail_json"], dict):
        value = record["detail_json"].get("license_present")
        if value is not None:
            return bool(value)
    return None


def compute_pwin(record: dict[str, Any], coeffs: PwinCoefficients) -> float:
    """P(win) по модели коэффициентов (кап ``max_pwin_cap``)."""
    k_smp = coeffs.k_smp if _is_smp(record) else 1.0

    license_present = _license_present(record)
    if license_present is True:
        k_license = coeffs.k_license_present
    elif license_present is False:
        k_license = coeffs.k_license_absent
    else:
        k_license = 1.0

    k_large = coeffs.k_large if _nmck(record) > coeffs.k_large_threshold else 1.0

    kind = _procedure_kind(record)
    if kind == "auction":
        k_procedure = coeffs.k_procedure_auction
    elif kind == "contest":
        k_procedure = coeffs.k_procedure_contest
    elif kind == "quotation":
        k_procedure = coeffs.k_procedure_quotation
    else:
        k_procedure = 1.0

    k_ai = coeffs.k_ai if _is_ai(record, coeffs) else 1.0

    pwin = coeffs.base_pwin * k_smp * k_license * k_large * k_procedure * k_ai
    return round(min(pwin, coeffs.max_pwin_cap), 4)
