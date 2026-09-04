"""Тесты геодезического расстояния (Хаверсин)."""

from __future__ import annotations

import pytest

from zakupki_parser.geo.centers import GeoPoint
from zakupki_parser.geo.distance import distance_km


def test_distance_moscow_spb_about_635_km() -> None:
    moscow = GeoPoint(55.7558, 37.6173)
    spb = GeoPoint(59.9343, 30.3351)
    assert 600 < distance_km(moscow, spb) < 660


def test_distance_zero_for_same_point() -> None:
    point = GeoPoint(55.0, 37.0)
    assert distance_km(point, point) == pytest.approx(0.0)


def test_distance_symmetric() -> None:
    a = GeoPoint(55.0, 37.0)
    b = GeoPoint(60.0, 45.0)
    assert distance_km(a, b) == pytest.approx(distance_km(b, a))
