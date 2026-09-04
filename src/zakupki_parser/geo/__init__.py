"""Модуль геопозиционирования: геокодирование адресов/регионов и проверка расстояния.

Используется только при одновременном выполнении условий:
- профиль задал ``target_regions`` и ``max_region_distance_km`` (место поставки не
  дальше N км от центра целевых регионов);
- в конфигурации (``config_ops.yaml -> geocoding``) описан доступ к сервису
  геокодирования (``enabled`` + ``base_url`` + известный ``provider``);
- координаты центров целевых регионов определены (геокодируются на лету).

Если гео-фильтр неприменим, решение принимается прежним способом: строковой
фильтрацией по региону (см. ``parser.filtering.region_match``).
"""

from __future__ import annotations

from zakupki_parser.geo.centers import GeoPoint
from zakupki_parser.geo.distance import distance_km
from zakupki_parser.geo.geocoder import Geocoder, build_geocoder
from zakupki_parser.geo.region_filter import (
    geo_centers,
    geo_filter_ready,
    region_too_far,
    stored_delivery_point,
)

__all__ = [
    "GeoPoint",
    "Geocoder",
    "build_geocoder",
    "distance_km",
    "geo_centers",
    "geo_filter_ready",
    "region_too_far",
    "stored_delivery_point",
]
