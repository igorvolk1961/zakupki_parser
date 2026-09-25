"""SSRF-защита исходящих запросов по URL, заданным пользователем.

Используется загрузкой страницы для «Заполнить профиль по URL»
(``api/app/profile_source.py``) и сбором сайтов-источников
(``sources/crawler.py`` — проверяется каждый запрос браузера).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


class UnsafeUrlError(ValueError):
    """URL не разрешён: не http/https, нет хоста или хост — внутренний адрес."""


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True, если адрес — публичный маршрутизируемый IP (не внутренняя сеть)."""
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


async def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    return [ipaddress.ip_address(sockaddr[0]) for _, _, _, _, sockaddr in infos]


async def ensure_public_host(host: str) -> None:
    """Запрещает хосты, ведущие на внутренние/локальные адреса.

    Резолвит хост через DNS (или разбирает IP-литерал) и требует, чтобы ВСЕ
    полученные адреса были публичными. Не защищает от DNS rebinding (сервер
    меняет ответ между этой проверкой и фактическим запросом) — компромисс
    ради простоты: вызывающие — аутентифицированные пользователи приложения.
    """
    if not host:
        raise UnsafeUrlError("В URL не указан хост")
    try:
        infos = await _resolve(host)
    except OSError as exc:
        raise UnsafeUrlError(f"Не удалось разрешить адрес «{host}»") from exc
    if not infos:
        raise UnsafeUrlError(f"Не удалось разрешить адрес «{host}»")
    if not all(is_public_ip(ip) for ip in infos):
        raise UnsafeUrlError("URL указывает на внутренний/локальный адрес — такие адреса запрещены")


async def ensure_public_url(url: str) -> None:
    """http/https и публичный хост."""
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError("Поддерживаются только http/https URL")
    await ensure_public_host(parts.hostname or "")
