"""Тесты клиента геокодирования: DaData (qc_geo-фильтр) и кэш."""

from __future__ import annotations

import httpx

from zakupki_parser.geo.centers import GeoPoint
from zakupki_parser.geo.geocoder import CachedGeocoder, DadataGeocoder, NominatimGeocoder


def _dadata(handler: httpx.MockTransport) -> DadataGeocoder:
    return DadataGeocoder(
        base_url="https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest",
        api_key="key",
        timeout=5.0,
        max_retries=0,
        backoff=0.0,
        rate=100.0,
        user_agent="test",
        transport=handler,
    )


async def test_dadata_returns_house_level() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        return httpx.Response(
            200,
            json={
                "suggestions": [
                    {"data": {"geo_lat": "55.7", "geo_lon": "37.6", "qc_geo": "2"}},
                    {"data": {"geo_lat": "55.71", "geo_lon": "37.61", "qc_geo": "1"}},
                ]
            },
        )

    point = await _dadata(httpx.MockTransport(handler)).geocode(
        "г Москва, ул Тверская, д 8", min_quality=1
    )
    assert point == GeoPoint(55.71, 37.61)


async def test_dadata_rejects_above_quality() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"suggestions": [{"data": {"geo_lat": "55.7", "geo_lon": "37.6", "qc_geo": "2"}}]},
        )

    point = await _dadata(httpx.MockTransport(handler)).geocode("г Москва", min_quality=1)
    assert point is None


async def test_dadata_empty_query_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("не должен вызываться")

    point = await _dadata(httpx.MockTransport(handler)).geocode("   ", min_quality=1)
    assert point is None


async def test_nominatim_takes_first_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "q" in request.url.params
        return httpx.Response(200, json=[{"lat": "55.7", "lon": "37.6"}])

    geo = NominatimGeocoder(
        base_url="https://nominatim.openstreetmap.org",
        timeout=5.0,
        max_retries=0,
        backoff=0.0,
        rate=100.0,
        user_agent="test",
        transport=httpx.MockTransport(handler),
    )
    point = await geo.geocode("Москва", min_quality=1)
    assert point == GeoPoint(55.7, 37.6)


async def test_cached_geocoder_hits_cache() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"suggestions": [{"data": {"geo_lat": "55.7", "geo_lon": "37.6", "qc_geo": "1"}}]},
        )

    inner = _dadata(httpx.MockTransport(handler))
    geo = CachedGeocoder(inner)
    first = await geo.geocode("г Москва, ул Тверская, д 8", min_quality=1)
    second = await geo.geocode("  Г Москва, ул ТВЕРСКАЯ, д 8 ", min_quality=1)
    assert first == second == GeoPoint(55.7, 37.6)
    assert calls == 1


async def test_cached_geocoder_does_not_cache_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"suggestions": []})

    geo = CachedGeocoder(_dadata(httpx.MockTransport(handler)))
    assert await geo.geocode("нет такого адреса 1234", min_quality=1) is None
    assert await geo.geocode("нет такого адреса 1234", min_quality=1) is None
    assert calls == 2
