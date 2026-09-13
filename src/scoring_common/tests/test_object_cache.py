"""Unit-тесты L2-кэша текста документов в S3/MinIO (scoring_common.tz.object_cache)."""

from __future__ import annotations

from typing import Any

import pytest

from scoring_common.tz import object_cache as oc


@pytest.fixture(autouse=True)
def _reset_client_cache():
    oc.reset_client_cache()
    yield
    oc.reset_client_cache()


def test_settings_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TZ_CACHE_ENABLED", raising=False)
    settings = oc.S3CacheSettings()
    assert settings.enabled is False
    assert settings.bucket == "tz-text-cache"


def test_client_none_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("TZ_CACHE_ENABLED", raising=False)
    assert oc._client() is None


def test_client_none_when_enabled_but_no_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TZ_CACHE_ENABLED", "true")
    monkeypatch.delenv("TZ_CACHE_ENDPOINT_URL", raising=False)
    assert oc._client() is None


def test_get_cached_text_returns_none_when_client_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(oc, "_client", lambda: None)
    assert oc.get_cached_text("http://x/tz.pdf") is None


def test_put_cached_text_noop_when_client_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(oc, "_client", lambda: None)
    # Не должно бросать исключение — просто ничего не делает.
    oc.put_cached_text("http://x/tz.pdf", "текст")


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    """Заглушка boto3 S3-клиента: словарь (bucket, key) -> bytes."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}
        self.raise_on_get = False
        self.raise_on_put = False

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if self.raise_on_get:
            raise RuntimeError("S3 недоступен")
        data = self.store.get((Bucket, Key))
        if data is None:
            raise KeyError("NoSuchKey")
        return {"Body": _FakeBody(data)}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> None:  # noqa: N803
        if self.raise_on_put:
            raise RuntimeError("S3 недоступен")
        self.store[(Bucket, Key)] = Body


def test_put_then_get_roundtrip(monkeypatch) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(oc, "_client", lambda: fake)
    oc.put_cached_text("http://x/tz.pdf", "текст технического задания")
    assert oc.get_cached_text("http://x/tz.pdf") == "текст технического задания"
    # Ключ в хранилище — sha256 от cache_key, не сырой URL.
    (bucket, key), _ = next(iter(fake.store.items()))
    assert bucket == "tz-text-cache"
    assert key == oc._object_key("http://x/tz.pdf")


def test_get_cached_text_miss_returns_none(monkeypatch) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(oc, "_client", lambda: fake)
    assert oc.get_cached_text("http://x/nope.pdf") is None


def test_get_cached_text_swallows_client_errors(monkeypatch) -> None:
    fake = _FakeS3Client()
    fake.raise_on_get = True
    monkeypatch.setattr(oc, "_client", lambda: fake)
    assert oc.get_cached_text("http://x/tz.pdf") is None


def test_put_cached_text_swallows_client_errors(monkeypatch) -> None:
    fake = _FakeS3Client()
    fake.raise_on_put = True
    monkeypatch.setattr(oc, "_client", lambda: fake)
    # Не должно бросать наружу.
    oc.put_cached_text("http://x/tz.pdf", "текст")
    assert fake.store == {}


def test_object_key_is_sha256_hex() -> None:
    import hashlib

    key = oc._object_key("http://x/tz.pdf")
    assert key == hashlib.sha256(b"http://x/tz.pdf").hexdigest()
    # Разные ключи -> разные хэши (не коллизия/не идентичность).
    assert oc._object_key("http://x/other.pdf") != key
