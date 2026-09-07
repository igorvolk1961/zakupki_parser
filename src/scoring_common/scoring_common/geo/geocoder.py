"""Async-клиент облачного сервиса геокодирования (DaData / Nominatim).

Абстракция ``Geocoder`` позволяет переключать провайдера только конфигом. Каждый
провайдер реализует доступ к координатам; ``CachedGeocoder`` оборачивает его кэшем
по нормализованному запросу (центры регионов геокодируются один раз, адреса — по
hash) и сериализует запросы под лимит ``rate_limit_per_second``.

Интеграция — ``build_geocoder``: возвращает ``None``, если доступ к сервису не
описан (модуль геопозиционирования не активируется). Конфиг — ``GeocodingConfig``,
независимый от конкретного сервиса (см. analysis_service).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from scoring_common.geo.centers import GeoPoint

logger = logging.getLogger("scoring_common.geo.geocoder")


class GeocodingConfig(BaseModel):
    """Доступ к сервису геокодирования (провайдер, лимиты, точность).

    API-ключ секретен и в конфиг не попадает: читается из env по ``key_env``
    (по образцу прочих секретов проекта).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="включает модуль геопозиционирования. False — гео-фильтр не используется",
    )
    provider: str = Field(default="dadata", description="провайдер: dadata | nominatim")
    base_url: str | None = Field(
        default=None,
        description=(
            "базовый URL сервиса. Для DaData: "
            "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address; "
            "для Nominatim: https://nominatim.openstreetmap.org. Пусто — модуль не активен"
        ),
    )
    min_result_quality: int = Field(
        default=1,
        ge=0,
        le=4,
        description="максимально допустимый qc_geo результата (0 — точные, 1 — дом, 2 — улица…)",
    )
    timeout_seconds: float = Field(default=10.0, gt=0, description="таймаут запроса, сек")
    max_retries: int = Field(default=2, ge=0, description="повторы при сетевой ошибке/5xx/429")
    retry_backoff_seconds: float = Field(default=2.0, ge=0, description="пауза перед повтором, сек")
    rate_limit_per_second: float = Field(
        default=1.0, gt=0, description="вежливый режим (политика Nominatim <= 1 req/сек)"
    )
    user_agent: str = Field(default="zakupki-parser/0.5")
    key_env: str = Field(default="GEO_API_KEY", description="env-переменная с API-ключом")


class Geocoder(Protocol):
    """Геокодирование запроса (адрес/регион) в координаты.

    ``min_quality`` — максимально допустимый уровень точности (qc_geo Дадаты:
    0 — точные, 1 — дом, 2 — улица, 3 — нас. пункт, 4 — город). Провайдеры,
    не возвращающие код качества (Nominatim), игнорируют параметр.
    """

    async def geocode(self, query: str, *, min_quality: int) -> GeoPoint | None: ...


class _BaseGeocoder:
    """Сериализация запросов (rate limit) + ретраи с экспоненциальным backoff."""

    def __init__(
        self,
        *,
        timeout: float,
        max_retries: int,
        backoff: float,
        rate: float,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = max(backoff, 0.0)
        self._min_interval = 1.0 / max(rate, 0.1)
        self._user_agent = user_agent
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        """Сериализует вызовы и выдерживает минимальный интервал между запросами."""
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
                transport=self._transport,
            )
        return self._client

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            await self._throttle()
            try:
                resp = await client.request(method, url, **kwargs)
                # Транзиентные сбои (429/5xx) — ретраим. Постоянная 4xx (401 неверный
                # ключ, 400 и т.п.) повтором не лечится: не тратим время, сразу
                # прокидываем ошибку. Caller (geocode) переводит её в fail-open None.
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"геокодер вернул {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and status < 500 and status != 429:
                    raise
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff * (2**attempt))
        assert last_exc is not None
        raise last_exc


class DadataGeocoder(_BaseGeocoder):
    """DaData «Подсказки»: детализация адресов РФ до дома (ФИАС/ГАР)."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout: float,
        max_retries: int,
        backoff: float,
        rate: float,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            timeout=timeout,
            max_retries=max_retries,
            backoff=backoff,
            rate=rate,
            user_agent=user_agent,
            transport=transport,
        )
        self._base = base_url.rstrip("/")
        self._api_key = api_key or ""

    async def geocode(self, query: str, *, min_quality: int) -> GeoPoint | None:
        text = query.strip()
        if not text:
            return None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Token {self._api_key}"
        try:
            resp = await self._request(
                "POST",
                f"{self._base}/suggestions/api/4_1/rs/suggest/address",
                json={"query": text, "count": 3},
                headers=headers,
            )
        except httpx.HTTPError as exc:
            # Внешний сервис недоступен/ошибка — fail-open: гео-решение не принимается,
            # закупка не теряется (caller трактует None как «любое расстояние»).
            logger.warning("Геокодер DaData недоступен («%s»): %s", text, exc)
            return None
        try:
            payload = resp.json()
        except ValueError:
            logger.warning("DaData вернул не-JSON ответ: %s", resp.text[:120])
            return None
        if not isinstance(payload, dict):
            return None
        for item in payload.get("suggestions", []):
            if not isinstance(item, dict):
                continue
            data = item.get("data", {})
            if not isinstance(data, dict):
                continue
            lat, lon = data.get("geo_lat"), data.get("geo_lon")
            if lat is None or lon is None:
                continue
            qc = data.get("qc_geo")
            if qc is not None and int(qc) > min_quality:
                continue
            return GeoPoint(float(lat), float(lon))
        return None


class NominatimGeocoder(_BaseGeocoder):
    """Nominatim (OpenStreetMap): бесплатно и без ключа; покрытие домов — по OSM."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        max_retries: int,
        backoff: float,
        rate: float,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            timeout=timeout,
            max_retries=max_retries,
            backoff=backoff,
            rate=rate,
            user_agent=user_agent,
            transport=transport,
        )
        self._base = base_url.rstrip("/")

    async def geocode(self, query: str, *, min_quality: int) -> GeoPoint | None:
        del min_quality  # Nominatim не отдаёт код качества: принимаем любой результат.
        text = query.strip()
        if not text:
            return None
        try:
            resp = await self._request(
                "GET",
                f"{self._base}/search",
                params={"q": text, "format": "json", "limit": 1, "countrycodes": "ru"},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Геокодер Nominatim недоступен («%s»): %s", text, exc)
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("Nominatim вернул не-JSON ответ: %s", resp.text[:120])
            return None
        if not isinstance(data, list) or not data:
            return None
        item = data[0]
        if not isinstance(item, dict):
            return None
        lat, lon = item.get("lat"), item.get("lon")
        if lat is None or lon is None:
            return None
        return GeoPoint(float(lat), float(lon))


class CachedGeocoder:
    """Кэш успешных результатов поверх любого ``Geocoder``.

    Кэшируем только положительный результат: сбой (транзиентная сетевая ошибка) не
    запоминается, чтобы следующий вызов для того же адреса повторил запрос. Ключ —
    нормализованный (lower + strip, пробелы вокруг дефиса) запрос и ``min_quality``.
    """

    def __init__(self, inner: Geocoder) -> None:
        self._inner = inner
        self._cache: dict[tuple[str, int], GeoPoint] = {}

    @staticmethod
    def _key(query: str, min_quality: int) -> tuple[str, int]:
        normalized = " ".join(query.strip().lower().replace(" - ", " ").split())
        return normalized, min_quality

    async def geocode(self, query: str, *, min_quality: int) -> GeoPoint | None:
        key = self._key(query, min_quality)
        if key in self._cache:
            return self._cache[key]
        point = await self._inner.geocode(query, min_quality=min_quality)
        if point is not None:
            self._cache[key] = point
        return point


def build_geocoder(cfg: GeocodingConfig) -> Geocoder | None:
    """Собирает геокодер из конфига; ``None`` — доступ к сервису не описан.

    Модуль геопозиционирования не активируется, если ``enabled=False``, не задан
    ``base_url`` или провайдер неизвестен. API-ключ для DaData читается из env
    ``cfg.key_env`` (в конфигах секреты не хранятся).
    """
    if not cfg.enabled or not cfg.base_url:
        return None
    api_key = os.environ.get(cfg.key_env) or None
    if cfg.provider == "dadata":
        inner: Geocoder = DadataGeocoder(
            cfg.base_url,
            api_key,
            timeout=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            backoff=cfg.retry_backoff_seconds,
            rate=cfg.rate_limit_per_second,
            user_agent=cfg.user_agent,
        )
    elif cfg.provider == "nominatim":
        inner = NominatimGeocoder(
            cfg.base_url,
            timeout=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            backoff=cfg.retry_backoff_seconds,
            rate=cfg.rate_limit_per_second,
            user_agent=cfg.user_agent,
        )
    else:
        logger.warning("Неизвестный провайдер геокодирования: %s", cfg.provider)
        return None
    return CachedGeocoder(inner)
