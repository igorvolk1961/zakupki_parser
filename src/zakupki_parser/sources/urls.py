"""Нормализация URL сайта-источника: один сайт — одна запись и один текст."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Метки рекламных кампаний и счётчиков — не меняют содержимое страницы.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"yclid", "gclid", "fbclid", "_openstat", "from"}


def normalize_source_url(url: str) -> str:
    """Схема и хост — в нижнем регистре, без стандартного порта, без ``#…``,
    без меток ``utm_*``/``gclid``/``yclid``, без завершающего ``/`` пути."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
        host = f"{host}:{port}"
    path = parts.path.rstrip("/")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
        ]
    )
    return urlunsplit((scheme, host, path, query, ""))
