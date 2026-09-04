"""Геодезическое расстояние между точками (формула Хаверсина), км.

Без внешних зависимостей (геопота в проекте нет): для порога в десятки
километров точность Хаверсина (ошибка < 0.5% на больших расстояниях) заведомо
достаточна.
"""

from __future__ import annotations

import math

from zakupki_parser.geo.centers import GeoPoint

_EARTH_RADIUS_KM = 6371.0088


def distance_km(a: GeoPoint, b: GeoPoint) -> float:
    """Расстояние между ``a`` и ``b`` по большому кругу, км."""
    phi1 = math.radians(a.lat)
    phi2 = math.radians(b.lat)
    dphi = math.radians(b.lat - a.lat)
    dlambda = math.radians(b.lon - a.lon)
    value = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(value))
