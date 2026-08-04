"""Unit-тесты хранилища файлов (local и s3/MinIO)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from zakupki_parser.config.models import StorageConfig
from zakupki_parser.storage.object_store import (
    LocalObjectStore,
    S3ObjectStore,
    build_object_store,
)


@pytest.mark.asyncio
async def test_local_store_put_delete(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    ref = await store.put("123/ТЗ.pdf", b"data", "application/pdf")
    assert (tmp_path / "123" / "ТЗ.pdf").read_bytes() == b"data"
    assert ref.filename == "ТЗ.pdf"
    assert ref.url.endswith("123/ТЗ.pdf")

    await store.delete(ref.key)
    assert not (tmp_path / "123" / "ТЗ.pdf").exists()
    # опустевшая папка удалена
    assert not (tmp_path / "123").exists()


def test_build_store_local(tmp_path: Path) -> None:
    store = build_object_store(StorageConfig(type="local"), tmp_path)
    assert isinstance(store, LocalObjectStore)


def test_build_store_s3(tmp_path: Path) -> None:
    store = build_object_store(
        StorageConfig(
            type="s3",
            endpoint="http://localhost:9000",
            access_key="k",
            secret_key="s",
            bucket="b",
        ),
        tmp_path,
    )
    assert isinstance(store, S3ObjectStore)


@pytest.mark.asyncio
async def test_s3_store_put_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3  # noqa: F401

    calls: dict[str, Any] = {}

    class FakeS3:
        def head_bucket(self, **kw: Any) -> None:  # noqa: ANN401
            raise RuntimeError("no bucket")

        def create_bucket(self, **kw: Any) -> None:  # noqa: ANN401
            calls["create"] = kw["Bucket"]

        def put_object(self, **kw: Any) -> None:  # noqa: ANN401
            calls["put"] = {"bucket": kw["Bucket"], "key": kw["Key"], "body": kw["Body"]}

    def fake_client(*args: Any, **kwargs: Any) -> FakeS3:  # noqa: ANN401
        return FakeS3()

    monkeypatch.setattr(boto3, "client", fake_client)
    store = S3ObjectStore(
        StorageConfig(type="s3", endpoint="http://minio:9000", access_key="k", secret_key="s")
    )
    ref = await store.put("123/ТЗ.pdf", b"data")
    assert ref.url == "http://minio:9000/zakupki-documents/123/ТЗ.pdf"
    assert calls["create"] == "zakupki-documents"
    assert calls["put"]["key"] == "123/ТЗ.pdf"
