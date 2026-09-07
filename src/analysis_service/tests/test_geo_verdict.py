"""Тесты гео-вердикта на этапе анализа (место поставки не дальше N км)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from analysis_service.pipeline.prompts import build_geo_address_messages
from analysis_service.worker import AnalysisWorker

from scoring_common.geo.centers import GeoPoint

_MOSCOW = GeoPoint(55.7558, 37.6173)
_SPB = GeoPoint(59.9343, 30.3351)


class _StubGeocoder:
    def __init__(self, mapping: dict[str, GeoPoint]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    async def geocode(self, query: str, *, min_quality: int) -> GeoPoint | None:
        self.calls.append(query.strip().lower())
        return self._mapping.get(query.strip().lower())


class _StubParser:
    def __init__(
        self,
        *,
        regions: list[str] | None = None,
        max_km: float | None = None,
        profile_cache: dict[str, Any] | None = None,
        procurement_geo: dict[str, Any] | None = None,
    ) -> None:
        self.regions = regions or []
        self.max_km = max_km
        self.profile_cache = profile_cache or {"regions": [], "centers": []}
        self.procurement_geo = procurement_geo or {
            "delivery_lat": None,
            "delivery_lon": None,
            "region": "Москва",
        }
        self.put_calls: list[tuple[str, Any]] = []
        self.put_profile_calls: list[tuple[int, list[str], list[dict[str, float]]]] = []

    async def get_active_client(
        self, internal_token: str | None = None, profile_id: int | None = None
    ) -> dict[str, Any]:
        return {"target_regions": self.regions, "max_region_distance_km": self.max_km}

    async def get_client_geo(self, profile_id: int) -> dict[str, Any]:
        return self.profile_cache

    async def put_client_geo(
        self, profile_id: int, regions: list[str], centers: list[dict[str, float]]
    ) -> dict[str, Any]:
        self.put_profile_calls.append((profile_id, regions, centers))
        return {"profile_id": profile_id, "regions": regions, "centers": centers}

    async def get_procurement_geo(self, procurement_id: int) -> dict[str, Any]:
        return self.procurement_geo

    async def put_procurement_geo(
        self, procurement_id: int, lat: float, lon: float
    ) -> dict[str, Any]:
        self.put_calls.append(("procurement", procurement_id))
        return {"delivery_lat": lat, "delivery_lon": lon, "region": "Москва"}


def _worker(geocoder: _StubGeocoder, parser: _StubParser) -> AnalysisWorker:
    w = AnalysisWorker.__new__(AnalysisWorker)
    w._settings = SimpleNamespace(parser_internal_token="t", geo_min_result_quality=1)  # type: ignore[attr-defined]
    w._parser = parser  # type: ignore[attr-defined]
    w._geocoder = geocoder  # type: ignore[attr-defined]
    return w


async def test_geo_verdict_within_and_caches() -> None:
    """Центры перегеокодируются (пустой кэш) и сохраняются; too_far=False."""
    geo = _StubGeocoder({"москва": _MOSCOW})
    parser = _StubParser(regions=["Москва"], max_km=400.0)
    w = _worker(geo, parser)
    record = {"region": "Москва"}
    verdict = await w._geo_verdict(record, 1618, 21)  # type: ignore[attr-defined]
    assert verdict is not None
    assert verdict["too_far"] is False
    assert verdict["max_distance_km"] == 400.0
    assert verdict["region"] == "Москва"
    assert parser.put_profile_calls == [(21, ["Москва"], [{"lat": 55.7558, "lon": 37.6173}])]
    assert parser.put_calls == [("procurement", 1618)]


async def test_geo_verdict_reuses_procurement_coords() -> None:
    """Координаты закупки уже в БД — не перегеокодируем (треб. 1)."""
    geo = _StubGeocoder({"москва": _MOSCOW})
    parser = _StubParser(
        regions=["Москва"],
        max_km=400.0,
        procurement_geo={"delivery_lat": 55.7558, "delivery_lon": 37.6173, "region": "Москва"},
    )
    w = _worker(geo, parser)
    verdict = await w._geo_verdict({"region": "Москва"}, 1618, 21)  # type: ignore[attr-defined]
    assert verdict is not None
    assert verdict["too_far"] is False
    assert geo.calls == ["москва"]  # только центр региона, место поставки НЕ геокодировалось
    assert parser.put_calls == []  # сохранение координат закупки не потребовалось


async def test_geo_verdict_reuses_profile_center_cache() -> None:
    """Набор регионов не изменился — кэш центров используется (треб. 2)."""
    geo = _StubGeocoder({})
    parser = _StubParser(
        regions=["Москва"],
        max_km=400.0,
        profile_cache={"regions": ["Москва"], "centers": [{"lat": 55.7558, "lon": 37.6173}]},
        procurement_geo={"delivery_lat": 55.7558, "delivery_lon": 37.6173, "region": "Москва"},
    )
    w = _worker(geo, parser)
    verdict = await w._geo_verdict({"region": "Москва"}, 1618, 21)  # type: ignore[attr-defined]
    assert verdict is not None
    assert verdict["too_far"] is False
    assert geo.calls == []  # геокодер не вызывался вовсе (кэш профиля + кэш закупки)
    assert parser.put_profile_calls == []


def test_geo_address_messages_placeholder_substituted() -> None:
    """Из промптов извлекается место поставки: address в системном, ТЗ в пользовательском."""
    system, user = build_geo_address_messages("Место поставки: г. Москва")
    assert "address" in system
    assert "Место поставки: г. Москва" in user


async def test_geo_verdict_none_without_constraint() -> None:
    """Нет дистанции — гео-проверка не применима (None)."""
    w = _worker(_StubGeocoder({}), _StubParser(regions=["Москва"], max_km=None))
    assert await w._geo_verdict({"region": "Москва"}, 1, 1) is None  # type: ignore[attr-defined]


async def test_geo_verdict_none_when_region_far_but_geocoder_unavailable() -> None:
    """Геокодер не знает регионов — fail-open (None), закупка не теряется."""
    geo = _StubGeocoder({})
    parser = _StubParser(regions=["Москва"], max_km=400.0)
    w = _worker(geo, parser)
    assert await w._geo_verdict({"region": "Москва"}, 1618, 21) is None  # type: ignore[attr-defined]
