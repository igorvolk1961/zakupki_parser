"""Гарантирует импортируемость пакета scoring_common из тестов (без установки)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
