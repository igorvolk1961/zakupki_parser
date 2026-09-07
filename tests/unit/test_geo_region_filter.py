"""Тесты решающего слоя гео-фильтра и гейта активации."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from scoring_common.geo.centers import GeoPoint
from scoring_common.geo.geocoder import GeocodingConfig
from scoring_common.geo.region_filter import (
    geo_centers,
    geo_filter_ready,
    region_too_far,
    stored_delivery_point,
)


class _StubGeocoder:
    """Геокодер-заглушка: точный адрес -> заданная точка, иначе None."""

    def __init__(self, mapping: dict[str, GeoPoint]) -> None:
        self._mapping = mapping

    async def geocode(self, query: str, *, min_quality: int) -> GeoPoint | None:
        return self._mapping.get(query.strip().lower())


def _profile(regions: list[str], max_km: float | None) -> Any:
    return SimpleNamespace(target_regions=regions, max_region_distance_km=max_km)


def _cfg(enabled: bool = True) -> GeocodingConfig:
    return GeocodingConfig(enabled=enabled)


def test_geo_filter_ready_requires_distance_and_regions() -> None:
    assert geo_filter_ready(_cfg(), _profile(["Москва"], 50.0)) is True
    assert geo_filter_ready(_cfg(), _profile(["Москва"], None)) is False
    assert geo_filter_ready(_cfg(False), _profile(["Москва"], 50.0)) is False
    assert geo_filter_ready(_cfg(), _profile([], 50.0)) is False


async def test_geo_centers_returns_all_points() -> None:
    geo = _StubGeocoder({"москва": GeoPoint(55.7558, 37.6173)})
    points = await geo_centers(["Москва"], geo)
    assert points == [GeoPoint(55.7558, 37.6173)]


async def test_geo_centers_empty_if_region_unknown() -> None:
    geo = _StubGeocoder({"москва": GeoPoint(55.7558, 37.6173)})
    points = await geo_centers(["Москва", "Неважное"], geo)
    assert points == []


def test_region_too_far_true_when_beyond_max() -> None:
    center = GeoPoint(55.7558, 37.6173)
    delivery = GeoPoint(59.9343, 30.3351)  # далёкая точка (СПб)
    assert region_too_far(delivery, [center], 5.0) is True


def test_region_too_far_false_within_max() -> None:
    center = GeoPoint(55.7558, 37.6173)
    assert region_too_far(center, [center], 1000.0) is False


def test_region_too_far_false_when_no_centers() -> None:
    assert region_too_far(GeoPoint(55.0, 37.0), [], 5.0) is False


def test_stored_delivery_point_returns_point() -> None:
    assert stored_delivery_point(55.7558, 37.6173) == GeoPoint(55.7558, 37.6173)


def test_stored_delivery_point_none_when_missing() -> None:
    assert stored_delivery_point(None, 37.6173) is None
    assert stored_delivery_point(55.7558, None) is None
    assert stored_delivery_point(None, None) is None
