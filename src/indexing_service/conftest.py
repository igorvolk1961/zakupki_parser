"""Гарантирует импортируемость пакетов indexing_service и scoring_common из тестов."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent / "scoring_common"))


@pytest.fixture(autouse=True)
def _object_storage_in_memory() -> Iterator[None]:
    """Объектное хранилище в тестах — в памяти, не MinIO из .env разработчика.

    Хранилище обязательно (``scoring_common.object_storage``); без подмены тесты
    либо падали бы на проверке при старте, либо ходили бы в реальный MinIO.
    """
    from scoring_common import object_storage

    object_storage.use_in_memory()
    yield
    object_storage.set_client(None)
