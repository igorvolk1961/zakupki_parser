"""CLI транспорта скоринга.

Subcommands:
  serve    — запуск FastAPI (ingest + фоновый consumer результатов);
  consumer — фоновый consumer результатов (отдельный процесс);
  enqueue  — ручная постановка задачи на скоринг (по id и optional priority).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from scoring_transport.settings import Settings, get_settings


def _logging_setup() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


logger = logging.getLogger(__name__)


def _cmd_serve(settings: Settings, host: str, port: int) -> int:
    import uvicorn

    from scoring_transport.web.app import create_app

    uvicorn.run(create_app(settings), host=host, port=port)
    return 0


async def _cmd_consumer(settings: Settings) -> int:
    from scoring_transport.consumers.results import run_consumer

    await run_consumer(settings)
    return 0


async def _cmd_enqueue(settings: Settings, procurement_id: int, priority: float | None) -> int:
    from scoring_transport.broker.redis_queue import TransportQueue
    from scoring_transport.parser_api import ParserApiClient
    from scoring_transport.scorer import priority_for

    if priority is None:
        # Приоритет по умолчанию — дефолтный score карточки (как в REST-ingest).
        try:
            card = await ParserApiClient(settings.parser_api_url).get_procurement(procurement_id)
            priority = priority_for(card, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось получить карточку %s, использую priority_default: %s",
                procurement_id,
                exc,
            )
            priority = settings.priority_default

    queue = TransportQueue(settings)
    await queue.connect()
    try:
        await queue.enqueue(procurement_id, priority)
        print(f"proc:{procurement_id} enqueued with priority {priority}")
    finally:
        await queue.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scoring-transport")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="запустить FastAPI (ingest + consumer)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8200)

    sub.add_parser("consumer", help="фоновый возврат результатов в парсер")

    p_enq = sub.add_parser("enqueue", help="ручная постановка задачи")
    p_enq.add_argument("procurement_id", type=int)
    p_enq.add_argument("--priority", type=float, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    _logging_setup()
    args = build_parser().parse_args(argv)
    settings = get_settings()

    if args.command == "serve":
        return _cmd_serve(settings, args.host, args.port)
    if args.command == "consumer":
        return asyncio.run(_cmd_consumer(settings))
    if args.command == "enqueue":
        return asyncio.run(_cmd_enqueue(settings, args.procurement_id, args.priority))
    return 2


if __name__ == "__main__":
    sys.exit(main())
