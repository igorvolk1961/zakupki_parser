"""Команды CLI парсера (диспетчеризация и исполнение)."""

from __future__ import annotations

import argparse
import sys

from zakupki_parser.cli.summary import _print_summary
from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig
from zakupki_parser.logging_conf import setup_logging


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

    if cmd == "coverage":
        return await _coverage(cfg, cfg_dir, args.platform)

    # Авто-миграции БД (Liquibase через CLI/подпроцесс) перед работой с БД.
    if cmd in ("run-once", "run-service"):
        from zakupki_parser.migrations import run_migrations

        run_migrations(cfg_dir, cfg.ops.db)

    from zakupki_parser.scheduler import Scheduler

    scheduler = Scheduler(cfg)
    if cmd == "run-once":
        await scheduler.start()
        try:
            # Одиночный проход считаем итерацией 1 (run-service ведёт счёт с 1):
            # так закупки, поставленные в очередь этим проходом, попадают в батч
            # журнала «Метрики», даже без циклического запуска.
            await scheduler.run_once(1)
        finally:
            await scheduler.stop()
        return 0
    if cmd == "run-service":
        await scheduler.run_service()
        return 0
    return 1


async def _coverage(cfg: AppConfig, cfg_dir: str, platform_id: str | None) -> int:
    """Печатает оценку покрытия полей: статику (конфиг) и динамику (БД, если включена)."""
    from zakupki_parser.cli.coverage import print_platform_static, render_runtime_row

    platforms = cfg.dom.platforms
    if platform_id:
        if platform_id not in platforms:
            print(f"Площадка {platform_id!r} не найдена в конфиге", file=sys.stderr)
            return 1
        ids = [platform_id]
    else:
        ids = list(platforms)

    for pid in ids:
        print_platform_static(platforms[pid], pid)

    if cfg.ops.db.enabled:
        from zakupki_parser.storage.db import Database
        from zakupki_parser.storage.repository import ProcurementRepository

        # Read-only диагностика: миграции не запускаем (они применяют DDL к целевой
        # БД); схема должна быть актуальной — иначе запрос просто завершится ошибкой.
        db = Database(cfg.ops.db)
        await db.connect()
        try:
            rows = await ProcurementRepository(db).field_coverage_runtime()
            by_platform = {r["platform_id"]: r for r in rows}
            print("\nДинамическое покрытие (по сохранённым записям):")
            for pid in ids:
                print("\n".join(render_runtime_row(pid, by_platform.get(pid))))
        finally:
            await db.dispose()
    else:
        print("\nБД отключена — динамическое покрытие недоступно (только статика)")
    return 0


def _serve(cfg_dir: str, host: str, port: int) -> int:
    """Запускает FastAPI-сервис (uvicorn)."""
    import uvicorn

    cfg = load_config(cfg_dir)
    setup_logging(cfg.logging)

    from zakupki_parser.migrations import run_migrations

    run_migrations(cfg_dir, cfg.ops.db)

    from zakupki_parser.api.app import create_app

    app = create_app(cfg_dir, port=port)
    # log_config=None: uvicorn использует наш root-логгер (config_log.yaml), чтобы
    # логи/ошибки (в т.ч. access и ASGI-ошибки) попадали в файл лога, а не только
    # в консоль.
    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0
