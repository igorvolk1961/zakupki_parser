"""Тесты конфигурации геокодирования и фабрики ``build_geocoder``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zakupki_parser.config.models.ops.geocoding import GeocodingConfig
from zakupki_parser.geo.geocoder import CachedGeocoder, build_geocoder


def test_geocoding_config_defaults() -> None:
    cfg = GeocodingConfig()
    assert cfg.enabled is False
    assert cfg.provider == "dadata"
    assert cfg.base_url is None
    assert cfg.min_result_quality == 1
    assert cfg.key_env == "ZAKUPKI_GEO_API_KEY"


def test_geocoding_config_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        GeocodingConfig.model_validate(
            {"enabled": True, "base_url": "http://x", "typo_key": "boom"}
        )


def test_geocoding_quality_bounds() -> None:
    assert GeocodingConfig(min_result_quality=0).min_result_quality == 0
    assert GeocodingConfig(min_result_quality=4).min_result_quality == 4
    with pytest.raises(ValidationError):
        GeocodingConfig(min_result_quality=5)


@pytest.mark.parametrize(
    ("cfg", "key", "expected"),
    [
        (GeocodingConfig(enabled=False, base_url="http://x"), "k", None),
        (GeocodingConfig(enabled=True, base_url=None), "k", None),
        (GeocodingConfig(enabled=True, base_url="http://x", provider="unknown"), "k", None),
        (GeocodingConfig(enabled=True, base_url="http://x"), "k", CachedGeocoder),
    ],
)
def test_build_geocoder_gating(
    monkeypatch: pytest.MonkeyPatch, cfg: GeocodingConfig, key: str, expected: type | None
) -> None:
    monkeypatch.setenv(cfg.key_env, key)
    geocoder = build_geocoder(cfg)
    if expected is None:
        assert geocoder is None
    else:
        assert isinstance(geocoder, expected)


def test_build_geocoder_dadata_reads_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZAKUPKI_GEO_API_KEY", "secret")
    cfg = GeocodingConfig(enabled=True, base_url="http://suggestions")
    assert isinstance(build_geocoder(cfg), CachedGeocoder)
