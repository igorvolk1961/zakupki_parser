"""Загрузка и валидация YAML-конфигов."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from dotenv import load_dotenv

from zakupki_parser.config.models import (
    AppConfig,
    DomConfig,
    LoggingConfig,
    ParserConfig,
    ScoreConfig,
    ServiceConfig,
)

CONFIG_FILES = {
    "parser": "config_parser.yaml",
    "service": "config_service.yaml",
    "logging": "config_log.yaml",
    "score": "config_score.yaml",
}

# Каталог с конфигами площадок (по одному YAML на площадку; имя файла = platform_id).
DOM_CONFIGS_DIR = "dom"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Конфиг-файл не найден: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Конфиг {path} должен быть YAML-словарём")
    return data


def _load_dom_configs(base: Path) -> dict[str, Any]:
    """Собирает ``platforms`` из ``configs/dom/*.yaml``.

    Каждый файл в подкаталоге ``dom`` описывает одну площадку: имя файла (без
    расширения) — ``platform_id``, содержимое — сам блок площадки. Для обратной
    совместимости (тестовый набор ``tests/configs``, имитатор) при отсутствии
    подкаталога читается единый ``config_dom.yaml`` с ключом ``platforms``.
    """
    dom_dir = base / DOM_CONFIGS_DIR
    if dom_dir.is_dir():
        platforms: dict[str, Any] = {}
        for path in sorted(dom_dir.glob("*.yaml")):
            platform_id = path.stem
            data = _load_yaml(path)
            if platform_id in platforms:
                raise ValueError(f"Дубликат platform_id в configs/{DOM_CONFIGS_DIR}: {path}")
            platforms[platform_id] = data
        return {"platforms": platforms}

    legacy = base / "config_dom.yaml"
    if not legacy.is_file():
        raise FileNotFoundError(
            f"Нет конфигов площадок: нет каталога configs/{DOM_CONFIGS_DIR} и файла config_dom.yaml"
        )
    data = _load_yaml(legacy)
    if "platforms" not in data:
        raise ValueError("config_dom.yaml должен содержать ключ platforms")
    return data


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


def load_config(configs_dir: str | Path) -> AppConfig:
    """Загружает все конфиги из ``configs_dir`` и возвращает ``AppConfig``."""
    base = Path(configs_dir).expanduser().resolve()

    # Секреты из .env в корне проекта (переменные окружения приоритетнее).
    load_dotenv(base.parent / ".env")

    parser_data = _load_yaml(base / CONFIG_FILES["parser"])
    dom_data = _load_dom_configs(base)
    service_data = _load_yaml(base / CONFIG_FILES["service"])
    logging_data = _load_yaml(base / CONFIG_FILES["logging"])
    score_data = _load_yaml(base / CONFIG_FILES["score"])

    # chat_id каналов можно задать из env (как и токены) — подставляем ДО
    # валидации, т.к. включённый бэкенд без chat_id — ошибка конфигурации.
    _inject_chat_ids(service_data)

    service_model = ServiceConfig.model_validate(service_data)
    logging_model = LoggingConfig.model_validate(logging_data)
    dom_model = DomConfig.model_validate(dom_data)
    score_model = ScoreConfig.model_validate(score_data)

    # Относительный путь файла лога — относительно корня проекта (родителя configs).
    if logging_model.file:
        log_path = Path(logging_model.file)
        if not log_path.is_absolute():
            log_path = base.parent / log_path
        logging_model.file = str(log_path)

    # Относительный путь маппинга ОКПД2 — относительно корня проекта.
    for platform in dom_model.platforms.values():
        if platform.search and platform.search.okpd_tree_file:
            tree_path = Path(platform.search.okpd_tree_file)
            if not tree_path.is_absolute():
                tree_path = base.parent / tree_path
            platform.search.okpd_tree_file = str(tree_path)

    # Переопределение через переменные окружения (для Docker/CI).
    env_dsn = os.environ.get("ZAKUPKI_DB_DSN")
    if env_dsn:
        service_model.db.dsn = env_dsn

    # Путь к исполняемому файлу Chromium — из env (имеет приоритет над YAML).
    env_chromium = os.environ.get("ZAKUPKI_CHROMIUM_EXECUTABLE")
    if env_chromium:
        parser_data.setdefault("browser", {})["chromium_executable_path"] = env_chromium

    # Секрет токена Telegram-бота — только из env, не хранится в YAML.
    env_token = os.environ.get("ZAKUPKI_TELEGRAM_TOKEN")
    if env_token:
        service_model.notifications.telegram.token = env_token

    # Секрет токена MAX-бота — только из env, не хранится в YAML.
    env_max_token = os.environ.get("ZAKUPKI_MAX_TOKEN")
    if env_max_token:
        service_model.notifications.max.token = env_max_token

    # Бэкенд уведомлений — из env (имеет приоритет над YAML). 'none' — выключить.
    env_backend = os.environ.get("ZAKUPKI_NOTIFY_BACKEND")
    if env_backend in ("telegram", "webhook", "max", "none"):
        service_model.notifications.backend = cast(
            Literal["telegram", "webhook", "max", "none"], env_backend
        )

    # Адрес scoring_transport — из env (имеет приоритет над YAML). В Docker это
    # имя сервиса (http://scoring-transport:8200), а не localhost.
    env_transport_url = os.environ.get("ZAKUPKI_SCORING_TRANSPORT_URL")
    if env_transport_url:
        score_model.scoring_transport_url = env_transport_url

    return AppConfig(
        configs_dir=base,
        parser=ParserConfig.model_validate(parser_data),
        dom=dom_model,
        service=service_model,
        logging=logging_model,
        score=score_model,
    )
