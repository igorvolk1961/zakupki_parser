"""Скачивание файлов ТЗ (с защитой от превышения размера и TTL-кэшем байт)."""

from __future__ import annotations

import logging
import ssl
import threading
import time
from collections import OrderedDict

import httpx

from scoring_common.tz.files import _MAX_FILE_BYTES

logger = logging.getLogger(__name__)

# Браузерный User-Agent: файлы ЭТП (например, etp.gpb.ru ``/file/get``) отдают
# ПУСТОЕ тело (200, 0 байт) на запрос без User-Agent (анти-бот), поэтому без него
# архив скачивается как b"" и листинг/извлечение ТЗ не находит файл.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Проверяем сертификат по СИСТЕМНОМУ хранилищу (как curl/браузер), а не по
# certifi-metadata: на ЭТП/Russian-host часто встречается TLS-перехват (VPN/
# корпоративный прокси) с самоподписанным промежуточным сертификатом, которому
# certifi не доверяет, а системный trust (и Playwright-парсер) — доверяет.
_SSL_CONTEXT = ssl.create_default_context()

# TTL кэша скачанных байт: один и тот же файл (в т.ч. архив для листинга и
# последующего извлечения записи) не должен скачиваться дважды за короткий срок.
_DOWNLOAD_TTL_SECONDS = 3600.0

# Потолки кэша байт: максимум записей (LRU) и суммарный бюджет байт — защита от
# неограниченного роста памяти на длинном процессе API.
_DOWNLOAD_MAX_ENTRIES = 64
_DOWNLOAD_MAX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 МБ суммарно

# Кэш: url (без #внутренний_путь) -> (время вставки, байты | None). Кэшируется
# и неуспех (None), чтобы временная недоступность ЭТП не вызывала повторных
# скачиваний при каждом запросе. OrderedDict — порядок для LRU-эвикции.
_download_cache: OrderedDict[str, tuple[float, bytes | None]] = OrderedDict()
_download_lock = threading.Lock()


def _prune_download_cache(now: float) -> None:
    """Очистить кэш байт: просроченные записи, LRU-лимит и бюджет байт.

    Вызывается только под ``_download_lock`` ПОСЛЕ вставки новой записи.
    """
    expired = [k for k, (ts, _) in _download_cache.items() if now - ts >= _DOWNLOAD_TTL_SECONDS]
    for key in expired:
        del _download_cache[key]
    while len(_download_cache) > _DOWNLOAD_MAX_ENTRIES:
        _download_cache.popitem(last=False)
    # Бюджет по сумме байт: вытесняем самые большие записи.
    while _download_cache:
        total = sum(len(data or b"") for _, data in _download_cache.values())
        if total <= _DOWNLOAD_MAX_TOTAL_BYTES:
            break
        largest_key = max(_download_cache, key=lambda k: len(_download_cache[k][1] or b""))
        del _download_cache[largest_key]


def clear_download_cache() -> None:
    """Очистить кэш скачанных байт (для тестов)."""
    with _download_lock:
        _download_cache.clear()


def _download(
    url: str, timeout: float = 30.0, max_bytes: int = _MAX_FILE_BYTES, verify_ssl: bool = True
) -> bytes | None:
    """Скачать файл (с защитой от превышения размера и TTL-кэшем).

    Кэш позволяет листингу архива и последующему извлечению записи из того же
    архива делить одно скачивание, а повторным открытиям карточки — не ходить
    в сеть заново. ``verify_ssl=False`` отключает проверку сертификата (для
    площадок за TLS-перехватом/VPN), по умолчанию — системный trust.
    """
    plain_url = url.split("#", 1)[0]
    now = time.monotonic()
    with _download_lock:
        cached = _download_cache.get(plain_url)
        if cached is not None and now - cached[0] < _DOWNLOAD_TTL_SECONDS:
            _download_cache.move_to_end(plain_url)
            return cached[1]
    raw: bytes | None = None
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _UA},
            verify=_SSL_CONTEXT if verify_ssl else False,
        ) as client:
            resp = client.get(plain_url)
            resp.raise_for_status()
            if int(resp.headers.get("content-length", "0") or 0) > max_bytes:
                raw = None
            else:
                raw = resp.content[:max_bytes]
    except httpx.HTTPError as exc:
        # Молчаливый None здесь превращается в «ТЗ не найдено» — логируем причину
        # (SSL-перехват/сеть/5xx), чтобы деградация была диагностируемой.
        logger.warning("Не удалось скачать файл ТЗ %s: %s", plain_url, exc)
        raw = None
    with _download_lock:
        _download_cache[plain_url] = (time.monotonic(), raw)
        _download_cache.move_to_end(plain_url)
        _prune_download_cache(time.monotonic())
    return raw
