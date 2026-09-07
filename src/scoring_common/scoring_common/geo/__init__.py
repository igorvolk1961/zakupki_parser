"""Модуль геопозиционирования (ScoringCommon): геокодер, центры регионов, дистанция.

Общий для сервисов: используется analysis_service на этапе анализа (место
поставки не дальше N км от центра целевого региона). Парсер гео-логики не содержит.
"""

from __future__ import annotations

from scoring_common.geo.centers import GeoPoint
from scoring_common.geo.distance import distance_km
from scoring_common.geo.geocoder import (
    CachedGeocoder,
    DadataGeocoder,
    Geocoder,
    GeocodingConfig,
    NominatimGeocoder,
    build_geocoder,
)
from scoring_common.geo.region_filter import (
    geo_centers,
    geo_filter_ready,
    region_too_far,
    stored_delivery_point,
)

__all__ = [
    "GeoPoint",
    "distance_km",
    "Geocoder",
    "GeocodingConfig",
    "DadataGeocoder",
    "NominatimGeocoder",
    "CachedGeocoder",
    "build_geocoder",
    "geo_centers",
    "geo_filter_ready",
    "region_too_far",
    "stored_delivery_point",
]
