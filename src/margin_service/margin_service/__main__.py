"""Точка входа ``python -m margin_service``."""

from __future__ import annotations

import sys

from margin_service.cli import main

if __name__ == "__main__":
    sys.exit(main())
