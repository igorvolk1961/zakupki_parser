"""Скачивание файлов ТЗ (с защитой от превышения размера)."""

from __future__ import annotations

import httpx

from scoring_common.tz.files import _MAX_FILE_BYTES


def _download(url: str, timeout: float = 30.0, max_bytes: int = _MAX_FILE_BYTES) -> bytes | None:
    """Скачать файл (с защитой от превышения размера)."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url.split("#", 1)[0])
            resp.raise_for_status()
            if int(resp.headers.get("content-length", "0") or 0) > max_bytes:
                return None
            return resp.content[:max_bytes]
    except httpx.HTTPError:
        return None
