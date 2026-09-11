"""CLI сервиса Index.

Subcommands:
  worker — фоновый воркер Redis-очереди (index:jobs -> index:results).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from indexing_service.settings import Settings, get_settings
from scoring_common.logging import setup_logging


async def _cmd_worker(settings: Settings) -> int:
    from indexing_service.worker import run_worker

    await run_worker(settings)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="indexing-service")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("worker", help="запустить фоновый воркер Redis-очереди")

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    setup_logging(settings.logging)
    args = build_parser().parse_args(argv)

    if args.command == "worker":
        return asyncio.run(_cmd_worker(settings))
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
