"""Вывод сводки по конфигурации (команда ``zp check-config``)."""

from __future__ import annotations

from pathlib import Path

from zakupki_parser.config.models import AppConfig
from zakupki_parser.config.models.fields import (
    coverage_score,
    missing_mandatory,
    static_field_coverage,
)


def _print_static_coverage(cfg: AppConfig) -> None:
    """Статическое покрытие полей по каждой площадке + предупреждения по MANDATORY."""
    if not cfg.dom.platforms:
        return
    print("Покрытие полей (статика, из конфига):")
    for pid, platform in cfg.dom.platforms.items():
        cov = static_field_coverage(platform)
        score = coverage_score(cov)
        missing = missing_mandatory(cov)
        mark = f"{score:.0%}"
        if missing:
            print(f"  {pid:<16} [{mark}]  ! незакрытые MANDATORY: {', '.join(missing)}")
        else:
            print(f"  {pid:<16} [{mark}]")
    print()


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
    dom_dir = Path(cfg.configs_dir) / "dom"
    if dom_dir.is_dir():
        dom_files = sorted(dom_dir.glob("*.yaml"))
        print(f"  {'dom/':<22} {len(dom_files):>7} файлов (площадки)")
        for path in dom_files:
            print(f"    - {path.stem}")
    print()

    # --- Покрытие полей (статика) ----------------------------------------
    _print_static_coverage(cfg)

    # --- Сервис ----------------------------------------------------------
    print("Сервис (config_service.yaml):")
    print(f"  Площадок в списке сайтов: {len(cfg.service.sites)}")
    for site in cfg.service.sites:
        mark = "вкл" if site.enabled else "ВЫКЛ"
        plat = cfg.dom.platforms.get(site.platform_id)
        name = plat.name if plat else "?"
        url = plat.url if plat else "?"
        print(f"    - {site.platform_id:<14} [{mark}]  {name} ({url})")
    print(
        "  Критерии поиска — из активного профиля (таблица profiles: okpd_codes/"
        "nmck_min/nmck_max; слова — таблица keywords, R9; выбор по состоянию "
        "active_only — глобальный search_criteria). Сид: zp seed-profile "
        "(файл-сид профиля)"
    )
    mode = "все площадки по дате" if cfg.service.sort_by_date_only else "по конфигурации площадок"
    print(f"  Порог дат (дней): {cfg.service.default_cutoff_days} (режим: {mode})")
    sc_cond = cfg.service.search_criteria
    print(f"  Stop-условия: deadline истёк: {_yn(sc_cond.deadline_not_expired)}")
    print(
        f"  Circuit breaker: порог сбоев {cfg.ops.circuit_breaker_failure_threshold}, "
        f"сброс {cfg.ops.circuit_breaker_reset_timeout_seconds} сек"
    )
    print()

    # --- Скоринг ---------------------------------------------------------
    print("Скоринг (config_score.yaml):")
    print(
        "  Дефолтный скор удалён; внешний каскад (fit/pwin/margin) — "
        "в procurement_evaluations (per-profile), приоритет очереди — время обновления."
    )
    print()

    # --- Уведомления -----------------------------------------------------
    notif = cfg.ops.notifications
    print("Уведомления (config_ops.yaml):")
    print(f"  Бэкенд: {notif.backend}")
    tg = notif.telegram
    mx = notif.max
    wh = notif.webhook
    print(f"  Telegram: {_yn(tg.enabled)}" + (f" (chat_id: {tg.chat_id})" if tg.enabled else ""))
    print(f"  MAX:      {_yn(mx.enabled)}" + (f" (chat_id: {mx.chat_id})" if mx.enabled else ""))
    print(f"  Webhook:  {_yn(wh.enabled)}" + (f" (url: {wh.url})" if wh.enabled else ""))
    print()

    # --- БД --------------------------------------------------------------
    db = cfg.ops.db
    print("БД (config_ops.yaml):")
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
