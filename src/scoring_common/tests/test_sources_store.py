"""Хранилище текстов сайтов-источников (scoring_common.sources.store)."""

from __future__ import annotations

from typing import Any

import pytest

from scoring_common import object_storage
from scoring_common.sources import store


def test_join_and_split_pages_roundtrip() -> None:
    pages = [("http://x/1", "строка 1\nстрока 2"), ("http://x/2", "строка 3")]
    text = store.join_pages(pages)
    assert "=== page 1: http://x/1 ===" in text
    assert store.split_pages(text) == pages


def test_put_get_and_missing() -> None:
    memory = object_storage.use_in_memory()
    key = store.text_key("https://x.ru/a")
    assert store.get_text(key) is None
    store.put_text(key, "текст")
    assert store.get_text(key) == "текст"
    assert next(iter(memory.store))[0] == "site-sources"


def test_keys_differ_per_url_and_kind() -> None:
    assert store.text_key("https://a") != store.text_key("https://b")
    assert store.text_key("https://a") != store.first_page_key("https://a")


class _Broken:
    def get_object(self, **kwargs: Any) -> Any:
        raise RuntimeError("сеть")

    def put_object(self, **kwargs: Any) -> Any:
        raise RuntimeError("сеть")


def test_storage_errors_propagate() -> None:
    """Текст сайта — данные, а не кэш: сбой хранилища не гасится."""
    object_storage.set_client(_Broken())
    with pytest.raises(RuntimeError):
        store.get_text("k")
    with pytest.raises(RuntimeError):
        store.put_text("k", "t")
