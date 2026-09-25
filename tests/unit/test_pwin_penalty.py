"""Снижение P(win) за мягкие барьеры вердикта (EvaluationMixin._apply_pwin_penalty)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from zakupki_parser.storage.repository.evaluations import EvaluationMixin


def _evaluation(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "p_win_base": 0.5,
        "p_win": 0.5,
        "fit_score": 8.0,
        "margin": None,
        "score": 4.0,
        "score_method": "pwin",
        "rag_report": None,
    }
    return SimpleNamespace(**{**base, **kw})


def _soft(n: int) -> dict[str, Any]:
    reasons = [{"source": f"field:f{i}", "label": f"поле {i}"} for i in range(n)]
    return {"verdict": {"accepted": True, "blocking_reasons": [], "soft_reasons": reasons}}


def test_no_soft_reasons_keeps_model_pwin() -> None:
    ev = _evaluation(rag_report=_soft(0))
    EvaluationMixin._apply_pwin_penalty(ev, 0.6)
    assert (ev.p_win, ev.score) == (0.5, 4.0)


def test_each_soft_reason_multiplies_pwin() -> None:
    ev = _evaluation(rag_report=_soft(2))
    EvaluationMixin._apply_pwin_penalty(ev, 0.6)
    assert ev.p_win == 0.18  # 0.5 × 0.6²
    assert ev.score == 1.44  # 8 × 0.18
    assert ev.p_win_base == 0.5


def test_margin_score_recomputed_from_components() -> None:
    ev = _evaluation(rag_report=_soft(1), score_method="margin", margin=0.2)
    EvaluationMixin._apply_pwin_penalty(ev, 0.6)
    assert ev.p_win == 0.3
    assert ev.score == pytest.approx(0.48)  # 8 × 0.3 × 0.2


def test_soft_reasons_gone_restores_pwin() -> None:
    """Пересчёт условий снял мягкий барьер — P(win) и score возвращаются к модели."""
    ev = _evaluation(rag_report=_soft(0), p_win=0.3, score=2.4)
    EvaluationMixin._apply_pwin_penalty(ev, 0.6)
    assert (ev.p_win, ev.score) == (0.5, 4.0)


def test_fit_stage_score_untouched() -> None:
    ev = _evaluation(rag_report=_soft(1), score_method="fit", score=8.0)
    EvaluationMixin._apply_pwin_penalty(ev, 0.6)
    assert ev.p_win == 0.3
    assert ev.score == 8.0


def test_without_model_pwin_nothing_changes() -> None:
    ev = _evaluation(rag_report=_soft(3), p_win_base=None, p_win=None, score=8.0)
    EvaluationMixin._apply_pwin_penalty(ev, 0.6)
    assert (ev.p_win, ev.score) == (None, 8.0)
