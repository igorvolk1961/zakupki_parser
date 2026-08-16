"""Гарантирует импортируемость пакетов pwin_service и scoring_common из тестов.

scoring_common — соседний подпроект: добавляется в sys.path, чтобы работали
``import scoring_common`` без установки.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent / "scoring_common"))
