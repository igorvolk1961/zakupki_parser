"""CLI сервиса P(win).

Subcommands:
  worker — фоновый воркер Redis-очереди (pwin:jobs → pwin:results);
  score  — разовый расчёт P(win) по файлу карточки (JSON) для отладки.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from pwin_service.settings import Settings, get_settings


def _logging_setup() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _cmd_worker(settings: Settings) -> int:
    from pwin_service.worker import run_worker

    await run_worker(settings)
    return 0


def _cmd_score(settings: Settings, card: Path) -> int:
    from scoring_common.pwin import compute_pwin

    record = json.loads(card.read_text(encoding="utf-8"))
    p_win = compute_pwin(record, settings)
    print(json.dumps({"p_win": p_win}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pwin-service")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("worker", help="запустить фоновый воркер Redis-очереди")

    p_score = sub.add_parser("score", help="разовый расчёт P(win) по карточке")
    p_score.add_argument("card", type=Path, help="путь к JSON-карточке закупки")

    return parser


def main(argv: list[str] | None = None) -> int:
    _logging_setup()
    args = build_parser().parse_args(argv)
    settings = get_settings()

    if args.command == "worker":
        return asyncio.run(_cmd_worker(settings))
    if args.command == "score":
        return _cmd_score(settings, args.card)
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
