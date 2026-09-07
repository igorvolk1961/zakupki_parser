"""Географическая точка (широта/долгота)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """Координаты точки на земной поверхности (WGS84), десятичные градусы."""

    lat: float
    lon: float
