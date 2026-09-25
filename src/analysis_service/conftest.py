"""Подключает общий пакет scoring_common (src/scoring_common) к тестам."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1]
_common = _SRC / "scoring_common"
if str(_common) not in sys.path:
    sys.path.insert(0, str(_common))


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
