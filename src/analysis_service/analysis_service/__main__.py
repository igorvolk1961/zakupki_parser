"""Точка входа: ``python -m analysis_service``."""

from __future__ import annotations

import sys

from analysis_service.cli import main

if __name__ == "__main__":
    sys.exit(main())
