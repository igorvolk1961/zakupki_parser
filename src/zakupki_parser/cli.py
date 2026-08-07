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
    parser = argparse.ArgumentParser(prog="zp")
    parser.add_argument(
        "--configs",
        default=str(DEFAULT_CONFIGS_DIR),
        help="Каталог с YAML-конфигами (по умолчанию: configs)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="Проверить конфигурацию")
    sub.add_parser("run-once", help="Один проход по всем площадкам")
    sub.add_parser("run-service", help="Периодический запуск по таймеру")

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

    # Авто-миграции БД (Liquibase через CLI/подпроцесс) перед работой с БД.
    if cmd in ("run-once", "run-service"):
        from zakupki_parser.migrations import run_migrations

        run_migrations(cfg_dir, cfg.service.db)

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


def _yn(value: bool) -> str:
    return "да" if value else "нет"


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 запись, 2 записи, 5 записей."""
    n10, n100 = n % 10, n % 100
    if 10 <= n100 <= 20:
        return many
    if n10 == 1:
        return one
    if 2 <= n10 <= 4:
        return few
    return many


def _mask_dsn(dsn: str) -> str:
    """DSN без пароля (для вывода в check-config)."""
    # postgresql+asyncpg://user:pass@host:port/db
    try:
        scheme, _, rest = dsn.partition("://")
        userinfo, _, after = rest.rpartition("@")
        if not after:
            return dsn
        user, _, _ = userinfo.partition(":")
        return f"{scheme}://{user}@{after}"
    except Exception:  # noqa: BLE001
        return dsn


def _print_summary(cfg: AppConfig) -> None:
    print("Конфигурация валидна.\n")

    # --- Файлы конфигурации ---------------------------------------------
    print("Файлы конфигурации:")
    for path in sorted(Path(cfg.configs_dir).glob("*.yaml")):
        size = path.stat().st_size
        print(f"  {path.name:<22} {size:>7} байт")
    print()

    # --- Сервис ----------------------------------------------------------
    print("Сервис (config_service.yaml):")
    print(f"  Площадок в списке сайтов: {len(cfg.service.sites)}")
    for site in cfg.service.sites:
        mark = "вкл" if site.enabled else "ВЫКЛ"
        plat = cfg.dom.platforms.get(site.platform_id)
        name = plat.name if plat else "?"
        url = plat.url if plat else "?"
        print(f"    - {site.platform_id:<14} [{mark}]  {name} ({url})")
    sc = cfg.service.search_criteria
    okpd = ", ".join(sc.okpd_codes) if sc.okpd_codes else "–"
    print(
        f"  Критерии поиска: ОКПД2={okpd}; "
        f"НМЦК {sc.nmck_min or '–'}…{sc.nmck_max or '–'}; "
        f"44-ФЗ={_yn(sc.fz44)}; 223-ФЗ={_yn(sc.fz223)}"
    )
    print(f"  Порог дат (дней): {cfg.service.default_cutoff_days}")
    print(f"  Директория данных: '{cfg.service.data_dir}'")
    sc_cond = cfg.service.stop_conditions
    min_days = sc_cond.min_deadline_days if sc_cond.min_deadline_days is not None else "–"
    print(
        f"  Stop-условия: {_yn(sc_cond.enabled)}"
        f" (deadline истёк: {_yn(sc_cond.deadline_not_expired)}; мин. дней до срока: {min_days})"
    )
    print(
        f"  Circuit breaker: порог сбоев {cfg.service.circuit_breaker_failure_threshold}, "
        f"сброс {cfg.service.circuit_breaker_reset_timeout_seconds} сек"
    )
    print()

    # --- Скоринг ---------------------------------------------------------
    score = cfg.score
    print("Скоринг (config_score.yaml):")
    print(f"  P(win): {score.p_win}; default_fit: {score.default_fit}")
    n_fit = len(score.fit_table)
    print(
        f"  fit-таблица (ОКПД2 → коэффициент): {n_fit} "
        f"{_plural(n_fit, 'запись', 'записи', 'записей')}"
    )
    print()

    # --- Уведомления -----------------------------------------------------
    notif = cfg.service.notifications
    print("Уведомления (config_service.yaml):")
    print(f"  Бэкенд: {notif.backend}")
    tg = notif.telegram
    mx = notif.max
    wh = notif.webhook
    print(f"  Telegram: {_yn(tg.enabled)}" + (f" (chat_id: {tg.chat_id})" if tg.enabled else ""))
    print(f"  MAX:      {_yn(mx.enabled)}" + (f" (chat_id: {mx.chat_id})" if mx.enabled else ""))
    print(f"  Webhook:  {_yn(wh.enabled)}" + (f" (url: {wh.url})" if wh.enabled else ""))
    print()

    # --- БД --------------------------------------------------------------
    db = cfg.service.db
    print("БД (config_service.yaml):")
    print(f"  Включена: {_yn(db.enabled)}")
    print(f"  Подключение: {_mask_dsn(db.dsn)}")
    print(
        f"  Пул: {db.pool_min}..{db.pool_max}; "
        f"таймаут подключения: {db.connect_timeout_seconds} сек"
    )
    attempts = _plural(db.retry_max_attempts, "попытка", "попытки", "попыток")
    print(f"  Ретраи: {db.retry_max_attempts} {attempts}, backoff {db.retry_backoff_seconds} сек")
    print()

    # --- Парсер / браузер ------------------------------------------------
    br = cfg.parser.browser
    print("Парсер / браузер (config_parser.yaml):")
    print(f"  Headless: {_yn(br.headless)}")
    print(f"  User-Agent: {br.user_agent or 'не задан (дефолт Chromium)'}")
    d1, d2 = br.delay_between_actions_seconds
    print(f"  Задержки между действиями: {d1}…{d2} сек")
    print(f"  Persistent session: {_yn(br.persist_session)} ({br.session_dir})")
    print(
        f"  Ignore HTTPS-errors: {_yn(br.ignore_https_errors)}; "
        f"stealth: {_yn(br.scroll_randomly or br.random_mouse_moves)}"
    )
    rl = cfg.parser.request_limits
    print(f"  Лимит запросов: {_yn(rl.enabled)} ({rl.max_requests_per_minute}/мин)")
    retry = cfg.parser.retry
    print(
        f"  Ретраи: до {retry.max_attempts}, backoff {retry.min_backoff_seconds}…"
        f"{retry.max_backoff_seconds} сек (джиттер {retry.jitter_seconds} сек)"
    )


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
                "Не удалось остановить процессы: " + ", ".join(str(pid) for pid in remaining),
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

    cfg = load_config(cfg_dir)
    setup_logging(cfg.logging)

    from zakupki_parser.migrations import run_migrations

    run_migrations(cfg_dir, cfg.service.db)

    from zakupki_parser.api.app import create_app

    app = create_app(cfg_dir)
    # log_config=None: uvicorn использует наш root-логгер (config_log.yaml), чтобы
    # логи/ошибки (в т.ч. access и ASGI-ошибки) попадали в файл лога, а не только
    # в консоль.
    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


if __name__ == "__main__":
    main()
