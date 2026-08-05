"""CLI-интерфейс парсера.

Команды:
- check-config   — проверить корректность YAML-конфигов
- run-once       — один проход по всем площадкам
- run-service    — периодический запуск по таймеру
- stop           — остановить запущенные процессы парсера
- capture-fixture— сохранить HTML страниц (список/деталь) в tests/fixtures
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig
from zakupki_parser.logging_conf import setup_logging

DEFAULT_CONFIGS_DIR = Path("configs")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zakupki-parser")
    parser.add_argument(
        "--configs",
        default=str(DEFAULT_CONFIGS_DIR),
        help="Каталог с YAML-конфигами (по умолчанию: configs)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-config", help="Проверить конфигурацию")
    sub.add_parser("run-once", help="Один проход по всем площадкам")
    sub.add_parser("run-service", help="Периодический запуск по таймеру")
    sub.add_parser("score-worker", help="Разовый запуск воркера внешнего скоринга")

    stop = sub.add_parser("stop", help="Остановить запущенные процессы парсера")
    stop.add_argument(
        "--force", action="store_true", help="убить сразу (SIGKILL) без мягкого закрытия"
    )

    serve = sub.add_parser("serve", help="Запустить FastAPI-сервис (API)")
    serve.add_argument("--host", default="0.0.0.0", help="адрес (по умолчанию 0.0.0.0)")
    serve.add_argument("--port", default=8000, type=int, help="порт (по умолчанию 8000)")

    cap = sub.add_parser("capture-fixture", help="Сохранение HTML-фикстур")
    cap.add_argument("--platform", default="zakupki_mos", help="platform_id из config_dom")
    cap.add_argument("--out", default="tests/fixtures", help="каталог вывода")
    return parser


async def _run(cmd: str, cfg_dir: str, args: argparse.Namespace) -> int:
    if cmd == "capture-fixture":
        from zakupki_parser.capture import capture_fixtures

        await capture_fixtures(cfg_dir, args.platform, args.out)
        return 0

    cfg = load_config(cfg_dir)
    setup_logging(cfg.logging)

    if cmd == "check-config":
        _print_summary(cfg)
        return 0

    from zakupki_parser.scheduler import Scheduler

    scheduler = Scheduler(cfg)
    if cmd == "run-once":
        await scheduler.start()
        try:
            await scheduler.run_once()
        finally:
            await scheduler.stop()
        return 0
    if cmd == "run-service":
        await scheduler.run_service()
        return 0
    if cmd == "score-worker":
        await scheduler.start()
        try:
            await scheduler.run_scoring_worker()
        finally:
            await scheduler.stop()
        return 0
    return 1


def _print_summary(cfg: AppConfig) -> None:
    print("Конфигурация валидна:")
    print(f"  Площадок в config_dom.yaml: {len(cfg.dom.platforms)}")
    print(f"  Площадок в списке сайтов:   {len(cfg.service.sites)}")
    print(f"  БД включена:                {cfg.service.db.enabled}")
    print(f"  Порог дат (дней):           {cfg.service.default_cutoff_days}")
    print(f"  Задержки между действиями:  {cfg.parser.browser.delay_between_actions_seconds}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "stop":
        from zakupki_parser.stopper import stop_parser

        try:
            remaining = stop_parser(force=args.force)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)
        if remaining:
            print(
                "Не удалось остановить процессы: "
                + ", ".join(str(pid) for pid in remaining),
                file=sys.stderr,
            )
            sys.exit(1)
        print("Парсер остановлен.")
        sys.exit(0)
    if args.command == "serve":
        code = _serve(args.configs, args.host, args.port)
        sys.exit(code)
    try:
        code = asyncio.run(_run(args.command, args.configs, args))
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        code = 1
    sys.exit(code)


def _serve(cfg_dir: str, host: str, port: int) -> int:
    """Запускает FastAPI-сервис (uvicorn)."""
    import uvicorn

    from zakupki_parser.api.app import create_app

    cfg = load_config(cfg_dir)
    setup_logging(cfg.logging)
    app = create_app(cfg_dir)
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    main()
