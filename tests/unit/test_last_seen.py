"""Unit-тесты хранилища даты последней обработки."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from zakupki_parser.storage.last_seen import LastSeenStore


def test_load_default(tmp_path: Path) -> None:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    store = LastSeenStore(tmp_path, default_cutoff_days=7)
    value = store.load("p1", now)
    assert (now - value).days == 7


def test_save_and_load(tmp_path: Path) -> None:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    store = LastSeenStore(tmp_path, default_cutoff_days=7)
    store.save("p1", now)
    assert store.load("p1", now) == now
