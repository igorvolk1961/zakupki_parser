"""Общее объектное хранилище S3/MinIO — обязательная зависимость сервисов.

Используется L2-кэшем извлечённого текста документов (``tz/object_cache.py``)
и хранилищем текстов сайтов-источников (``sources/store.py``). Хранилище не отключается:
сервисы, работающие с текстом документов (API, indexing_service,
scoring_service, analysis_service), при старте вызывают
``require_object_storage`` и не стартуют без настроенного и доступного S3.

Настройки — из окружения процесса (namespace ``OBJECT_STORAGE_``), не из
settings.py конкретного сервиса: один набор переменных на весь стек.

Тесты подменяют клиент хранилищем в памяти (``use_in_memory``) — реальный
MinIO из ``.env`` разработчика в тесты не попадает.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ObjectStorageUnavailable(RuntimeError):
    """Хранилище не настроено или недоступно — сервис не может стартовать."""


class ObjectStorageSettings(BaseSettings):
    """Подключение к S3-совместимому хранилищу и имена бакетов."""

    model_config = SettingsConfigDict(env_prefix="OBJECT_STORAGE_", extra="ignore")

    endpoint_url: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    region: str = "us-east-1"
    # Бакет L2-кэша извлечённого текста документов закупки.
    tz_cache_bucket: str = "tz-text-cache"
    # Бакет текстов сайтов-источников (scoring_common.sources.store).
    sources_bucket: str = "site-sources"

    def buckets(self) -> list[str]:
        """Все бакеты, которые должны существовать (проверяются при старте)."""
        return [self.tz_cache_bucket, self.sources_bucket]


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class InMemoryS3:
    """Подмена boto3 S3-клиента для тестов: словарь ``(bucket, key) -> bytes``.

    Реализует только используемое подмножество API (``get_object``,
    ``put_object``, ``head_bucket``). Любой бакет считается существующим.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        data = self.store.get((Bucket, Key))
        if data is None:
            raise KeyError(f"NoSuchKey: {Bucket}/{Key}")
        return {"Body": _Body(data)}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> dict[str, Any]:  # noqa: N803
        self.store[(Bucket, Key)] = bytes(Body)
        return {}

    def head_bucket(self, Bucket: str) -> dict[str, Any]:  # noqa: N803
        return {}


_lock = threading.Lock()
_override: Any = None
_client: Any = None


def set_client(client: Any) -> None:
    """Подменить клиент (тесты); ``None`` — вернуть реальный boto3-клиент."""
    global _override, _client
    with _lock:
        _override = client
        _client = None


def use_in_memory() -> InMemoryS3:
    """Подменить клиент хранилищем в памяти и вернуть его (тесты)."""
    client = InMemoryS3()
    set_client(client)
    return client


def get_settings() -> ObjectStorageSettings:
    return ObjectStorageSettings()


def get_client() -> Any:
    """S3-клиент (ленивый синглтон).

    Raises:
        ObjectStorageUnavailable: не задан ``OBJECT_STORAGE_ENDPOINT_URL``.
    """
    global _client
    with _lock:
        if _override is not None:
            return _override
        if _client is not None:
            return _client
        settings = get_settings()
        if not settings.endpoint_url:
            raise ObjectStorageUnavailable(
                "Объектное хранилище не настроено: задайте OBJECT_STORAGE_ENDPOINT_URL, "
                "OBJECT_STORAGE_ACCESS_KEY, OBJECT_STORAGE_SECRET_KEY (см. .env.example)"
            )
        import boto3

        _client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
        )
        return _client


def require_object_storage(
    buckets: Iterable[str] | None = None,
    *,
    attempts: int = 5,
    delay: float = 2.0,
) -> None:
    """Проверка при старте сервиса: хранилище настроено и все бакеты доступны.

    Несколько попыток с паузой — хранилище может подниматься одновременно с
    сервисом (docker compose). Синхронная: в async-коде вызывать через
    ``asyncio.to_thread``.

    Raises:
        ObjectStorageUnavailable: с понятной причиной (что не так и что задать).
    """
    client = get_client()
    settings = get_settings()
    names = list(buckets) if buckets is not None else settings.buckets()
    last_error: Exception | None = None
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            for bucket in names:
                client.head_bucket(Bucket=bucket)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                logger.warning(
                    "Объектное хранилище недоступно (попытка %d/%d): %s", attempt, attempts, exc
                )
                time.sleep(delay)
    raise ObjectStorageUnavailable(
        f"Объектное хранилище {settings.endpoint_url} недоступно или нет бакетов "
        f"{', '.join(names)}: {last_error}. Проверьте, что MinIO запущен и бакеты созданы "
        "(docker compose: сервис minio-init)"
    )
