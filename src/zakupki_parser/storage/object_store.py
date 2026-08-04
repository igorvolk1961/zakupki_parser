"""Абстракция хранилища скачанных файлов.

``local`` — каталог ``documents_dir`` (путь в БД); ``s3`` — MinIO/совместимое
объектное хранилище (в БД пишется URL объекта, а не бинарник).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zakupki_parser.config.models import StorageConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileRef:
    """Ссылка на сохранённый файл."""

    key: str
    url: str
    filename: str


class ObjectStore:
    """Сохранение/удаление файлов скачанных из закупок."""

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> FileRef:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalObjectStore(ObjectStore):
    """Локальный каталог (documents_dir). URL — абсолютный путь к файлу."""

    def __init__(self, documents_dir: Path) -> None:
        self._dir = documents_dir

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> FileRef:
        dest = self._dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return FileRef(key=str(key), url=str(dest.resolve()), filename=dest.name)

    async def delete(self, key: str) -> None:
        path = self._dir / key
        if path.is_file():
            path.unlink()
        # удаляем опустевшие родительские папки (от ближайшей вверх)
        parent = path.parent
        while parent != self._dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


class S3ObjectStore(ObjectStore):
    """MinIO/совместимое объектное хранилище (S3-протокол через boto3)."""

    def __init__(self, cfg: StorageConfig) -> None:
        self._cfg = cfg
        self._client = None

    def _s3(self) -> Any:
        import boto3

        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._cfg.endpoint,
                aws_access_key_id=self._cfg.access_key,
                aws_secret_access_key=self._cfg.secret_key,
                region_name=self._cfg.region,
                use_ssl=self._cfg.secure,
            )
        return self._client

    def _ensure_bucket(self) -> None:
        s3 = self._s3()
        try:
            s3.head_bucket(Bucket=self._cfg.bucket)
        except Exception:  # noqa: BLE001
            s3.create_bucket(Bucket=self._cfg.bucket)
            logger.info("Создан bucket %s", self._cfg.bucket)

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> FileRef:
        await asyncio.to_thread(self._ensure_bucket)
        await asyncio.to_thread(
            self._s3().put_object,
            Bucket=self._cfg.bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        # прямой URL: endpoint/bucket/key
        base = self._cfg.endpoint.rstrip("/")
        url = f"{base}/{self._cfg.bucket}/{key}"
        filename = key.rsplit("/", 1)[-1]
        logger.info("Загружен объект s3://%s/%s", self._cfg.bucket, key)
        return FileRef(key=key, url=url, filename=filename)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._s3().delete_object, Bucket=self._cfg.bucket, Key=key)


def build_object_store(cfg: StorageConfig, documents_dir: Path) -> ObjectStore:
    """Создаёт хранилище по конфигурации."""
    if cfg.type == "s3":
        return S3ObjectStore(cfg)
    return LocalObjectStore(documents_dir)
