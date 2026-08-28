"""CLI транспорта скоринга.

Subcommands:
  serve    — запуск FastAPI (ingest + фоновый consumer результатов);
  consumer — фоновый consumer результатов (отдельный процесс);
  enqueue  — ручная постановка задачи на скоринг (по id и optional priority).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from scoring_common.logging import setup_logging
from scoring_transport.settings import Settings, get_settings


def _cmd_serve(settings: Settings, host: str, port: int) -> int:
    import uvicorn

    from scoring_transport.web.app import create_app

    # Авторизация обязательна: сервис не стартует без токена (TRANSPORT_AUTH_TOKEN).
    if not settings.auth_token:
        raise SystemExit(
            "Ошибка: авторизация обязательна — задайте TRANSPORT_AUTH_TOKEN "
            "(иначе эндпоинт /api/scoring/jobs не защищён)"
        )
    uvicorn.run(create_app(settings), host=host, port=port)
    return 0


async def _cmd_consumer(settings: Settings) -> int:
    from scoring_transport.consumers.results import run_consumer

    await run_consumer(settings)
    return 0


async def _cmd_enqueue(
    settings: Settings, procurement_id: int, profile_id: int, priority: float | None
) -> int:
    from scoring_transport.broker.redis_queue import TransportQueue

    # Приоритет передаётся явно (из парсера, авто-пуш, ADR-7);
    # если не задан — берём priority_default из настроек.
    if priority is None:
        priority = settings.priority_default

    queue = TransportQueue(settings)
    await queue.connect()
    try:
        await queue.enqueue(procurement_id, priority, profile_id=profile_id)
        print(f"proc:{procurement_id} pf:{profile_id} enqueued with priority {priority}")
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
    p_enq.add_argument("profile_id", type=int)
    p_enq.add_argument("--priority", type=float, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    setup_logging(settings.logging)
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        return _cmd_serve(settings, args.host, args.port)
    if args.command == "consumer":
        return asyncio.run(_cmd_consumer(settings))
    if args.command == "enqueue":
        return asyncio.run(
            _cmd_enqueue(settings, args.procurement_id, args.profile_id, args.priority)
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
