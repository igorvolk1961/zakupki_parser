"""Подключает общий пакет scoring_common (src/scoring_common) к тестам."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
_common = _SRC / "scoring_common"
if str(_common) not in sys.path:
    sys.path.insert(0, str(_common))
