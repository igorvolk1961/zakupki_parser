"""Тесты формулы P(win) и Margin."""

from __future__ import annotations

from scoring_common.config import PwinCoefficients
from scoring_common.margin import compute_margin
from scoring_common.pwin import compute_pwin


def test_pwin_default_base_only() -> None:
    coeffs = PwinCoefficients()
    assert compute_pwin({"subject": "Разработка ПО", "nmck": 1_000_000}, coeffs) == 0.4


def test_pwin_large_nmck_applies_k_large() -> None:
    coeffs = PwinCoefficients()
    assert compute_pwin({"subject": "Разработка ПО", "nmck": 60_000_000}, coeffs) == 0.24


def test_pwin_ai_markers_in_subject() -> None:
    coeffs = PwinCoefficients()
    assert compute_pwin({"subject": "Разработка ИИ-агента", "nmck": 1_000}, coeffs) == 0.72


def test_pwin_ai_marker_in_okpd2() -> None:
    coeffs = PwinCoefficients()
    assert compute_pwin({"subject": "Услуги", "okpd2_codes": "62.01", "nmck": 1_000}, coeffs) == 0.4


def test_pwin_cap_applied() -> None:
    coeffs = PwinCoefficients(base_pwin=0.9, k_ai=1.8)
    assert compute_pwin({"subject": "ИИ-платформа", "nmck": 1_000}, coeffs) == 0.95


def test_pwin_procedure_from_subject() -> None:
    coeffs = PwinCoefficients()
    assert (
        compute_pwin({"subject": "Электронный аукцион: разработка", "nmck": 1_000}, coeffs) == 0.52
    )
    assert compute_pwin({"subject": "Открытый конкурс: разработка", "nmck": 1_000}, coeffs) == 0.4
    assert compute_pwin({"subject": "Запрос котировок: разработка", "nmck": 1_000}, coeffs) == 0.32


def test_pwin_smp_from_detail_json() -> None:
    coeffs = PwinCoefficients()
    record = {"subject": "Разработка", "nmck": 1_000, "detail_json": {"is_smp_only": True}}
    assert compute_pwin(record, coeffs) == 0.6


def test_pwin_license_absent_from_detail_json() -> None:
    coeffs = PwinCoefficients()
    record = {"subject": "Разработка", "nmck": 1_000, "detail_json": {"license_present": False}}
    assert compute_pwin(record, coeffs) == 0.04


def test_margin_is_nmck_times_rate() -> None:
    assert compute_margin({"nmck": 500.0}, 1.0) == 500.0
    assert compute_margin({"nmck": 100.0}, 0.5) == 50.0
    assert compute_margin({"subject": "без НМЦК"}, 1.0) == 0.0
