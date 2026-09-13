"""Межпроцессный L2-кэш извлечённого текста документов в S3/MinIO (best-effort).

``extract_text_cached`` (``tz/__init__.py``) кэширует извлечённый текст только
внутри одного процесса (``_tz_text_cache``, TTL~1ч). ``indexing_service``,
``scoring_service``, ``analysis_service`` и API — разные процессы/контейнеры,
один и тот же файл закупки при этом скачивается и конвертируется в каждом из
них заново. Этот модуль добавляет общий для всех процессов слой хранения:
объект в S3-совместимом хранилище (MinIO), ключ — хэш URL файла.

Отключён по умолчанию (``TZ_CACHE_ENABLED`` не задан) — поведение сервисов,
которые не настроили S3, не меняется. Любая ошибка обращения к S3 (сеть,
auth, отсутствующий бакет/объект) гасится здесь же: недоступность кэша
никогда не должна прерывать извлечение текста, только лишает его ускорения.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class S3CacheSettings(BaseSettings):
    """Настройки L2-кэша текста документов, читаются из окружения напрямую.

    Namespace ``TZ_CACHE_`` — не привязан к settings.py конкретного сервиса,
    чтобы все потребители ``scoring_common.tz`` получали кэш через переменные
    окружения процесса без отдельной прокидки параметров.
    """

    model_config = SettingsConfigDict(env_prefix="TZ_CACHE_", extra="ignore")

    enabled: bool = False
    endpoint_url: str | None = None
    bucket: str = "tz-text-cache"
    access_key: str | None = None
    secret_key: str | None = None
    region: str = "us-east-1"


def _object_key(cache_key: str) -> str:
    return hashlib.sha256(cache_key.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _client() -> Any:
    """Ленивый синглтон S3-клиента. ``None`` — кэш выключен/не настроен (не ошибка)."""
    settings = S3CacheSettings()
    if not settings.enabled or not settings.endpoint_url:
        return None
    try:
        import boto3
    except ImportError:  # pragma: no cover - boto3 объявлен как обязательная зависимость
        logger.debug("Кэш текста документов: boto3 не установлен, кэш выключен")
        return None
    try:
        return boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Кэш текста документов: не удалось создать S3-клиент", exc_info=True)
        return None


def reset_client_cache() -> None:
    """Сбросить синглтон клиента (тесты — переинициализация после monkeypatch окружения)."""
    _client.cache_clear()


def get_cached_text(cache_key: str) -> str | None:
    """Прочитать текст из S3 по ключу либо ``None`` (промах ИЛИ кэш недоступен)."""
    client = _client()
    if client is None:
        return None
    settings = S3CacheSettings()
    try:
        obj = client.get_object(Bucket=settings.bucket, Key=_object_key(cache_key))
        body: bytes = obj["Body"].read()
        return body.decode("utf-8")
    except Exception:  # noqa: BLE001
        logger.debug("Кэш текста документов: промах/ошибка чтения S3", exc_info=True)
        return None


def put_cached_text(cache_key: str, text: str) -> None:
    """Записать текст в S3 (write-through, best-effort — ошибка не пробрасывается)."""
    client = _client()
    if client is None:
        return
    settings = S3CacheSettings()
    try:
        client.put_object(
            Bucket=settings.bucket,
            Key=_object_key(cache_key),
            Body=text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
    except Exception:  # noqa: BLE001
        logger.debug("Кэш текста документов: не удалось записать в S3", exc_info=True)
