"""Модель конфигурации сервиса геокодирования (config_ops.yaml).

Несекретная часть доступа к облачному геокодеру. API-ключ в YAML не хранится и
читается из env (см. ``key_env``) — по образцу прочих секретов проекта
(``ZAKUPKI_AUTH_SECRET``, ``ZAKUPKI_INTERNAL_TOKEN``). Модуль геопозиционирования
активируется только если ``enabled`` и задан ``base_url`` с известным ``provider``.
"""

from __future__ import annotations

from pydantic import Field

from zakupki_parser.config.models.ops.base import _BaseConfig


class GeocodingConfig(_BaseConfig):
    """Доступ к сервису геокодирования координат адресов/регионов.

    Провайдеры (см. ``zakupki_parser.geo.geocoder.build_geocoder``):
    - ``dadata`` — DaData (ФИАС/ГАР), лучшая точность адресов РФ «до дома»;
    - ``nominatim`` — OpenStreetMap, бесплатно и без ключа, покрытие домов хуже.

    ``min_result_quality`` — порог точности координат (qc_geo Дадаты, чем меньше —
    тем точнее): 0 — точные, 1 — ближайший дом, 2 — улица, 3 — нас. пункт,
    4 — город. Для «места поставки» следует не выше 1 (до дома).
    """

    enabled: bool = Field(
        default=False,
        description=(
            "включает модуль геопозиционирования. False — гео-фильтр не используется, "
            "остаётся строковая фильтрация по региону"
        ),
    )
    provider: str = Field(
        default="dadata",
        description="провайдер геокодирования: dadata | nominatim",
    )
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
        description=(
            "максимально допустимый qc_geo результата (0 — точные, 1 — дом, 2 — улица, "
            "3 — нас. пункт, 4 — город). Значения больше порога отбрасываются: для "
            "«места поставки» это гарантирует детализацию до дома (0/1)"
        ),
    )
    timeout_seconds: float = Field(default=10.0, gt=0, description="таймаут запроса, сек")
    max_retries: int = Field(default=2, ge=0, description="повторы при сетевой ошибке/5xx/429")
    retry_backoff_seconds: float = Field(
        default=2.0, ge=0, description="пауза перед первым повтором, сек"
    )
    rate_limit_per_second: float = Field(
        default=1.0, gt=0, description="максимум запросов в секунду (политика Nominatim <= 1)"
    )
    user_agent: str = Field(
        default="zakupki-parser/0.5",
        description=(
            "User-Agent (обязателен для Nominatim / правил использования публичного инстанса)"
        ),
    )
    key_env: str = Field(
        default="ZAKUPKI_GEO_API_KEY",
        description="имя переменной окружения с API-ключом (секрет не хранится в YAML)",
    )
