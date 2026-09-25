"""Межпроцессный L2-кэш извлечённого текста документов в S3/MinIO.

``extract_text_cached`` (``tz/__init__.py``) кэширует извлечённый текст только
внутри одного процесса (``_tz_text_cache``, TTL~1ч). ``indexing_service``,
``scoring_service``, ``analysis_service`` и API — разные процессы/контейнеры,
один и тот же файл закупки при этом скачивается и конвертируется в каждом из
них заново. Этот модуль добавляет общий для всех процессов слой хранения:
объект в S3-совместимом хранилище (``scoring_common.object_storage``), ключ —
хэш URL файла.

Хранилище обязательно и не отключается: его наличие проверяется при старте
сервиса (``require_object_storage``). Отдельная операция чтения/записи при этом
best-effort — сбой обращения к S3 (сеть, отсутствующий объект) гасится здесь
же: он не должен прерывать извлечение текста, только лишает его ускорения.
"""

from __future__ import annotations

import hashlib
import logging

from scoring_common.object_storage import get_client, get_settings

logger = logging.getLogger(__name__)


def _object_key(cache_key: str) -> str:
    return hashlib.sha256(cache_key.encode("utf-8")).hexdigest()


def get_cached_text(cache_key: str) -> str | None:
    """Прочитать текст из S3 по ключу либо ``None`` (промах ИЛИ сбой обращения)."""
    try:
        obj = get_client().get_object(
            Bucket=get_settings().tz_cache_bucket, Key=_object_key(cache_key)
        )
        body: bytes = obj["Body"].read()
        return body.decode("utf-8")
    except Exception:  # noqa: BLE001
        logger.debug("Кэш текста документов: промах/ошибка чтения S3", exc_info=True)
        return None


def put_cached_text(cache_key: str, text: str) -> None:
    """Записать текст в S3 (write-through, best-effort — ошибка не пробрасывается)."""
    try:
        get_client().put_object(
            Bucket=get_settings().tz_cache_bucket,
            Key=_object_key(cache_key),
            Body=text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
    except Exception:  # noqa: BLE001
        logger.debug("Кэш текста документов: не удалось записать в S3", exc_info=True)
