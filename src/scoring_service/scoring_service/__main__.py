"""Точка входа ``python -m scoring_service``."""

from __future__ import annotations

import sys

from scoring_service.cli import main

if __name__ == "__main__":
    sys.exit(main())
