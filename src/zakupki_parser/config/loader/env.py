"""Инъекция секретов/переопределений из переменных окружения в конфиг."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast

from zakupki_parser.config.models import OpsConfig, ScoreConfig


def _inject_chat_ids(service_data: dict[str, Any]) -> None:
    """Подставляет chat_id каналов из env, если не заданы в YAML.

    Аналогично токенам: ``ZAKUPKI_MAX_CHAT_ID`` / ``ZAKUPKI_TELEGRAM_CHAT_ID``.
    Нужно выполнять до ``ServiceConfig.model_validate``, т.к. включённый бэкенд
    без chat_id — ошибка валидации.
    """
    notif = service_data.setdefault("notifications", {})
    if not isinstance(notif, dict):
        return
    for key, env_var in (("max", "ZAKUPKI_MAX_CHAT_ID"), ("telegram", "ZAKUPKI_TELEGRAM_CHAT_ID")):
        block = notif.get(key)
        if not isinstance(block, dict):
            continue
        if block.get("chat_id"):
            continue
        env_chat = os.environ.get(env_var)
        if env_chat:
            block["chat_id"] = env_chat


def _apply_auth_env(ops_data: dict[str, Any]) -> None:
    """Применяет параметры авторизации из env к ``ops_data`` ДО валидации.

    - ``ZAKUPKI_AUTH_SECRET`` — секрет подписи токенов (в YAML не хранится);
    - ``ZAKUPKI_INTERNAL_TOKEN`` — внутренний токен служебных эндпоинтов.

    Авторизация всегда включена, поэтому подстановка выполняется до
    ``OpsConfig.model_validate``: без секрета конфигурация отклоняется
    валидатором (fail fast), а не «включается» после валидации с пустым ключом
    подписи (HMAC над пустой строкой — подделка токенов).
    """
    auth = ops_data.setdefault("auth", {})
    if not isinstance(auth, dict):
        return
    env_secret = os.environ.get("ZAKUPKI_AUTH_SECRET")
    if env_secret:
        auth["secret"] = env_secret
    env_internal = os.environ.get("ZAKUPKI_INTERNAL_TOKEN")
    if env_internal:
        auth["internal_token"] = env_internal


def _apply_env_overrides(
    parser_data: dict[str, Any],
    ops_model: OpsConfig,
    score_model: ScoreConfig,
    base: Path,
) -> None:
    """Применяет переопределения из env к уже валидированным моделям (Docker/CI)."""
    # Переопределение через переменные окружения (для Docker/CI).
    env_dsn = os.environ.get("ZAKUPKI_DB_DSN")
    if env_dsn:
        ops_model.db.dsn = env_dsn

    # Путь к исполняемому файлу Chromium — из env (имеет приоритет над YAML).
    env_chromium = os.environ.get("ZAKUPKI_CHROMIUM_EXECUTABLE")
    if env_chromium:
        parser_data.setdefault("browser", {})["chromium_executable_path"] = env_chromium

    # Секрет токена Telegram-бота — только из env, не хранится в YAML.
    env_token = os.environ.get("ZAKUPKI_TELEGRAM_TOKEN")
    if env_token:
        ops_model.notifications.telegram.token = env_token

    # Секрет токена MAX-бота — только из env, не хранится в YAML.
    env_max_token = os.environ.get("ZAKUPKI_MAX_TOKEN")
    if env_max_token:
        ops_model.notifications.max.token = env_max_token

    # Бэкенд уведомлений — из env (имеет приоритет над YAML). 'none' — выключить.
    env_backend = os.environ.get("ZAKUPKI_NOTIFY_BACKEND")
    if env_backend in ("telegram", "webhook", "max", "none"):
        ops_model.notifications.backend = cast(
            Literal["telegram", "webhook", "max", "none"], env_backend
        )

    # Адрес scoring_transport — из env (имеет приоритет над YAML). В Docker это
    # имя сервиса (http://scoring-transport:8200), а не localhost.
    env_transport_url = os.environ.get("ZAKUPKI_SCORING_TRANSPORT_URL")
    if env_transport_url:
        score_model.scoring_transport_url = env_transport_url

    # Каталог промптов scoring_service — из env (имеет приоритет над YAML).
    # В Docker это общий том (например, /app/prompts), чтобы правки из
    # web-интерфейса видел scoring_service при следующем старте.
    prompts_dir = os.environ.get("ZAKUPKI_PROMPTS_DIR") or ops_model.prompts_dir
    prompts_path = Path(prompts_dir)
    if not prompts_path.is_absolute():
        prompts_path = base.parent / prompts_path
    ops_model.prompts_dir = str(prompts_path)

    # Каталог промптов analysis_service — из env (имеет приоритет над YAML).
    # В Docker это общий том (например, /app/analysis-prompts), чтобы правки из
    # web-интерфейса видел analysis_service при следующем старте.
    analysis_prompts_dir = (
        os.environ.get("ZAKUPKI_ANALYSIS_PROMPTS_DIR") or ops_model.analysis_prompts_dir
    )
    analysis_prompts_path = Path(analysis_prompts_dir)
    if not analysis_prompts_path.is_absolute():
        analysis_prompts_path = base.parent / analysis_prompts_path
    ops_model.analysis_prompts_dir = str(analysis_prompts_path)
