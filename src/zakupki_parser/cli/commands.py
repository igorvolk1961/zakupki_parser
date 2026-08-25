"""Команды CLI парсера (диспетчеризация и исполнение)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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

    if cmd == "seed-profile":
        return await _seed_profile(cfg, cfg_dir, args.user, Path(args.file))

    # Авто-миграции БД (Liquibase через CLI/подпроцесс) перед работой с БД.
    if cmd in ("run-once", "run-service"):
        from zakupki_parser.migrations import run_migrations

        run_migrations(cfg_dir, cfg.ops.db)

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
    return 1


async def _seed_profile(cfg: AppConfig, cfg_dir: str, username: str, file_path: Path) -> int:
    """Заполняет default-профиль пользователя словами/компетенциями из файла (R8).

    Файл (по умолчанию ``docs/references/profile.md``) содержит секции ``**keywords**``,
    ``**exclussion_words**``, ``**competencies**`` (см. ``keywords_parser``).
    """
    from zakupki_parser.migrations import run_migrations
    from zakupki_parser.storage.db import Database
    from zakupki_parser.storage.keywords_parser import parse_keywords_file
    from zakupki_parser.storage.repository import ProcurementRepository

    if not file_path.is_file():
        print(f"Файл не найден: {file_path}", file=sys.stderr)
        return 1
    parsed = parse_keywords_file(file_path)
    run_migrations(cfg_dir, cfg.ops.db)
    db = Database(cfg.ops.db)
    await db.connect()
    try:
        repo = ProcurementRepository(db)
        user = await repo.get_user_by_username(username)
        if user is None:
            print(f"Пользователь {username!r} не найден", file=sys.stderr)
            return 1
        profile_name = parsed.get("name") or "default"
        await repo.upsert_profile(
            {
                "name": profile_name,
                "enabled": True,
                "is_active": True,
                "competencies": parsed.get("competencies", ""),
                "keywords": parsed.get("keywords", []),
                "exclusion_words": parsed.get("exclusion_words", []),
                "okpd_codes": parsed.get("okpd_codes", []),
                "nmck_min": parsed.get("nmck_min"),
                "nmck_max": parsed.get("nmck_max"),
            },
            user.id,
        )
    finally:
        await db.dispose()
    n_kw = len(parsed.get("keywords", []))
    n_ex = len(parsed.get("exclusion_words", []))
    okpd = ", ".join(parsed.get("okpd_codes", [])) or "–"
    print(
        f"Профиль {profile_name!r} пользователя {username!r}: ключевых слов — {n_kw}, "
        f"минус-слов — {n_ex}, компетенции — "
        f"{'заданы' if parsed.get('competencies') else 'не заданы'}; "
        f"критерии: ОКПД2={okpd}, НМЦК {parsed.get('nmck_min') or '–'}…"
        f"{parsed.get('nmck_max') or '–'}"
    )
    return 0


def _serve(cfg_dir: str, host: str, port: int) -> int:
    """Запускает FastAPI-сервис (uvicorn)."""
    import uvicorn

    cfg = load_config(cfg_dir)
    setup_logging(cfg.logging)

    from zakupki_parser.migrations import run_migrations

    run_migrations(cfg_dir, cfg.ops.db)

    from zakupki_parser.api.app import create_app

    app = create_app(cfg_dir)
    # log_config=None: uvicorn использует наш root-логгер (config_log.yaml), чтобы
    # логи/ошибки (в т.ч. access и ASGI-ошибки) попадали в файл лога, а не только
    # в консоль.
    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0
