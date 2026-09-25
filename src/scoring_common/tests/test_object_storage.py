"""Unit-тесты обязательного объектного хранилища (scoring_common.object_storage)."""

from __future__ import annotations

from typing import Any

import pytest

from scoring_common import object_storage as os_

_ENV = (
    "OBJECT_STORAGE_ENDPOINT_URL",
    "OBJECT_STORAGE_ACCESS_KEY",
    "OBJECT_STORAGE_SECRET_KEY",
    "OBJECT_STORAGE_TZ_CACHE_BUCKET",
)


@pytest.fixture
def clean_env(monkeypatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    os_.set_client(None)


def test_settings_defaults(clean_env) -> None:
    settings = os_.ObjectStorageSettings()
    assert settings.endpoint_url is None
    assert settings.tz_cache_bucket == "tz-text-cache"
    assert settings.sources_bucket == "site-sources"
    assert settings.buckets() == ["tz-text-cache", "site-sources"]


def test_settings_new_names(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("OBJECT_STORAGE_TZ_CACHE_BUCKET", "b1")
    settings = os_.ObjectStorageSettings()
    assert settings.endpoint_url == "http://minio:9000"
    assert settings.tz_cache_bucket == "b1"


def test_get_client_raises_when_not_configured(clean_env) -> None:
    with pytest.raises(os_.ObjectStorageUnavailable, match="OBJECT_STORAGE_ENDPOINT_URL"):
        os_.get_client()


def test_require_raises_when_not_configured(clean_env) -> None:
    with pytest.raises(os_.ObjectStorageUnavailable):
        os_.require_object_storage(attempts=1)


def test_require_passes_with_in_memory() -> None:
    os_.use_in_memory()
    os_.require_object_storage(attempts=1)


class _FlakyS3(os_.InMemoryS3):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.calls: list[str] = []

    def head_bucket(self, Bucket: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append(Bucket)
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("connection refused")
        return {}


def test_require_retries_until_available() -> None:
    client = _FlakyS3(failures=2)
    os_.set_client(client)
    os_.require_object_storage(["a"], attempts=3, delay=0)
    assert client.calls == ["a", "a", "a"]


def test_require_fails_after_attempts_with_reason() -> None:
    os_.set_client(_FlakyS3(failures=10))
    with pytest.raises(os_.ObjectStorageUnavailable, match="connection refused"):
        os_.require_object_storage(["a"], attempts=2, delay=0)


def test_in_memory_roundtrip_and_miss() -> None:
    client = os_.InMemoryS3()
    client.put_object(Bucket="b", Key="k", Body=b"data")
    assert client.get_object(Bucket="b", Key="k")["Body"].read() == b"data"
    with pytest.raises(KeyError):
        client.get_object(Bucket="b", Key="missing")
