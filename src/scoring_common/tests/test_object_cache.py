"""Unit-тесты L2-кэша текста документов в S3/MinIO (scoring_common.tz.object_cache)."""

from __future__ import annotations

from typing import Any

import pytest

from scoring_common import object_storage as os_
from scoring_common.tz import object_cache as oc


class _FailingS3:
    """Клиент, у которого любое обращение падает (сеть/авторизация)."""

    def get_object(self, **kwargs: Any) -> Any:
        raise RuntimeError("S3 недоступен")

    def put_object(self, **kwargs: Any) -> Any:
        raise RuntimeError("S3 недоступен")


@pytest.fixture
def memory() -> os_.InMemoryS3:
    return os_.use_in_memory()


def test_put_then_get_roundtrip(memory: os_.InMemoryS3) -> None:
    oc.put_cached_text("http://x/tz.pdf", "текст технического задания")
    assert oc.get_cached_text("http://x/tz.pdf") == "текст технического задания"
    # Ключ в хранилище — sha256 от cache_key, не сырой URL.
    (bucket, key), _ = next(iter(memory.store.items()))
    assert bucket == "tz-text-cache"
    assert key == oc._object_key("http://x/tz.pdf")


def test_bucket_from_settings(monkeypatch, memory: os_.InMemoryS3) -> None:
    monkeypatch.setenv("OBJECT_STORAGE_TZ_CACHE_BUCKET", "custom-bucket")
    oc.put_cached_text("http://x/tz.pdf", "текст")
    assert next(iter(memory.store)) == ("custom-bucket", oc._object_key("http://x/tz.pdf"))


def test_get_cached_text_miss_returns_none(memory: os_.InMemoryS3) -> None:
    assert oc.get_cached_text("http://x/nope.pdf") is None


def test_get_cached_text_swallows_client_errors() -> None:
    os_.set_client(_FailingS3())
    assert oc.get_cached_text("http://x/tz.pdf") is None


def test_put_cached_text_swallows_client_errors() -> None:
    os_.set_client(_FailingS3())
    # Не должно бросать наружу.
    oc.put_cached_text("http://x/tz.pdf", "текст")


def test_operations_do_not_raise_when_storage_not_configured(monkeypatch) -> None:
    # Отсутствие настроек ловится проверкой при старте сервиса; отдельная
    # операция кэша остаётся best-effort и не роняет извлечение текста.
    os_.set_client(None)
    monkeypatch.delenv("OBJECT_STORAGE_ENDPOINT_URL", raising=False)
    assert oc.get_cached_text("http://x/tz.pdf") is None
    oc.put_cached_text("http://x/tz.pdf", "текст")


def test_object_key_is_sha256_hex() -> None:
    import hashlib

    key = oc._object_key("http://x/tz.pdf")
    assert key == hashlib.sha256(b"http://x/tz.pdf").hexdigest()
    # Разные ключи -> разные хэши (не коллизия/не идентичность).
    assert oc._object_key("http://x/other.pdf") != key
