"""Решающий слой гео-фильтра «место поставки не дальше N км от центра региона».

Содержит гейт активации и проверку расстояния. Решение откладывается (fail-open),
когда данные не позволяют принять его однозначно (нет адреса, центр региона не
геокодирован, недоступен геокодер) — чтобы не терять закупки из-за неполноты
гео-информации.
"""

from __future__ import annotations

import logging
from typing import Any

from scoring_common.geo.centers import GeoPoint
from scoring_common.geo.distance import distance_km
from scoring_common.geo.geocoder import Geocoder, GeocodingConfig

logger = logging.getLogger("scoring_common.geo.region_filter")

# Уровень точности для геокодирования центра региона: приемлемо до города (qc_geo 4).
_CENTER_QUALITY = 4


def geo_filter_ready(cfg: GeocodingConfig, profile: Any) -> bool:
    """Активирован ли гео-фильтр для профиля (модуль используется).

    Требуются одновременно: (1) профиль задал целевые регионы и макс. расстояние,
    (2) в конфигурации описан доступ к сервису геокодирования (``enabled``).
    """
    if not cfg.enabled:
        return False
    return bool(
        profile is not None
        and (getattr(profile, "target_regions", None) or [])
        and getattr(profile, "max_region_distance_km", None) is not None
    )


async def geo_centers(regions: list[str], geocoder: Geocoder) -> list[GeoPoint]:
    """Координаты центров целевых регионов.

    Геокодируются на лету (с кэшем в ``geocoder``); регионов мало и они стабильны,
    поэтому фактически — один запрос на регион. Если геокодировать удалось не все
    регионы, возвращается пустой список — гео-фильтр не активируется.
    """
    points: list[GeoPoint] = []
    for region in regions:
        point = await geocoder.geocode(str(region).strip(), min_quality=_CENTER_QUALITY)
        if point is None:
            logger.warning(
                "Координаты центра региона «%s» не определены — гео-фильтр отключён",
                region,
            )
            return []
        points.append(point)
    return points


def stored_delivery_point(lat: float | None, lon: float | None) -> GeoPoint | None:
    """Координаты места поставки закупки, сохранённые в БД (``procurements``).

    Возвращает ``None``, если координаты ещё не геокодированы (не сохранены).
    """
    if lat is None or lon is None:
        return None
    return GeoPoint(lat, lon)


def region_too_far(delivery: GeoPoint, centers: list[GeoPoint], max_km: float) -> bool:
    """True — место поставки дальше ``max_km`` от центра всех целевых регионов."""
    if not centers:
        return False
    nearest = min(distance_km(delivery, center) for center in centers)
    return nearest > max_km
