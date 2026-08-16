"""Точка входа ``python -m pwin_service``."""

from __future__ import annotations

import sys

from pwin_service.cli import main

if __name__ == "__main__":
    sys.exit(main())
