"""Загрузка и валидация YAML-конфигов."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from zakupki_parser.config.models import (
    AppConfig,
    DomConfig,
    LoggingConfig,
    ParserConfig,
    ServiceConfig,
)

CONFIG_FILES = {
    "parser": "config_parser.yaml",
    "dom": "config_dom.yaml",
    "service": "config_service.yaml",
    "logging": "config_log.yaml",
}


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


def load_config(configs_dir: str | Path) -> AppConfig:
    """Загружает все конфиги из ``configs_dir`` и возвращает ``AppConfig``."""
    base = Path(configs_dir).expanduser().resolve()

    parser_data = _load_yaml(base / CONFIG_FILES["parser"])
    dom_data = _load_yaml(base / CONFIG_FILES["dom"])
    service_data = _load_yaml(base / CONFIG_FILES["service"])
    logging_data = _load_yaml(base / CONFIG_FILES["logging"])

    service_model = ServiceConfig.model_validate(service_data)
    logging_model = LoggingConfig.model_validate(logging_data)

    # Относительный путь файла лога — относительно корня проекта (родителя configs).
    if logging_model.file:
        log_path = Path(logging_model.file)
        if not log_path.is_absolute():
            log_path = base.parent / log_path
        logging_model.file = str(log_path)

    # Переопределение через переменные окружения (для Docker/CI).
    env_dsn = os.environ.get("ZAKUPKI_DB_DSN")
    if env_dsn:
        service_model.db.dsn = env_dsn

    return AppConfig(
        configs_dir=base,
        parser=ParserConfig.model_validate(parser_data),
        dom=DomConfig.model_validate(dom_data),
        service=service_model,
        logging=logging_model,
    )
