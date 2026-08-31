"""Unit-тесты контракта полей и статического покрытия конфигурации."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zakupki_parser.config.loader import _load_dom_configs
from zakupki_parser.config.models import DomConfig
from zakupki_parser.config.models.fields import (
    FieldTier,
    coverage_score,
    missing_mandatory,
    required_fields,
    static_field_coverage,
)
from zakupki_parser.storage.db import Procurement

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"


def _platforms() -> dict[str, Any]:
    data = _load_dom_configs(CONFIGS_DIR)
    return DomConfig.model_validate(data).platforms


def test_required_fields_columns_exist() -> None:
    """Каждое поле контракта с column маппится на существующий атрибут Procurement."""
    for spec in required_fields():
        assert spec.tier in (
            FieldTier.MANDATORY,
            FieldTier.IMPORTANT,
            FieldTier.OPTIONAL,
        ), spec.key
        if spec.column is None:
            continue
        assert hasattr(Procurement, spec.column), f"{spec.key}: нет колонки {spec.column}"


def test_required_fields_keys_unique() -> None:
    keys = [f.key for f in required_fields()]
    assert len(keys) == len(set(keys)), "ключи контракта должны быть уникальными"


def test_static_coverage_runs_over_all_configs() -> None:
    """Покрытие вычисляется для всех площадок без ошибок в корректных пределах."""
    for pid, platform in _platforms().items():
        cov = static_field_coverage(platform)
        assert cov, f"{pid}: пустое покрытие"
        score = coverage_score(cov)
        assert 0.0 <= score <= 1.0, f"{pid}: score вне [0,1]"
        for status in cov:
            assert status.status in ("declared", "missing")
            assert status.tier in (
                FieldTier.MANDATORY,
                FieldTier.IMPORTANT,
                FieldTier.OPTIONAL,
            )


def test_fabrikant_covers_mandatory() -> None:
    """fabrikant заявляет все обязательные поля и все важные (по конфигу 2026-08-31)."""
    platforms = _platforms()
    cov = static_field_coverage(platforms["fabrikant"])
    assert missing_mandatory(cov) == []
    assert coverage_score(cov) == 1.0


def test_known_gap_detected() -> None:
    """b2b_center не заявляет ИНН (важное поле) — статика это фиксирует как missing."""
    platforms = _platforms()
    cov = static_field_coverage(platforms["b2b_center"])
    inn = next(s for s in cov if s.key == "inn")
    assert inn.status == "missing"
    assert "inn" not in missing_mandatory(cov), "inn — важное, не обязательное"
