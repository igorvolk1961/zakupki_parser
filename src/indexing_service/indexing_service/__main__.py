"""Точка входа ``python -m indexing_service``."""

from __future__ import annotations

import sys

from indexing_service.cli import main

if __name__ == "__main__":
    sys.exit(main())
