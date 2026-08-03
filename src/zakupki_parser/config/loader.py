"""Загрузка и валидация YAML-конфигов."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from zakupki_parser.config.models import (
    AppConfig,
    DomConfig,
    FiltersConfig,
    LoggingConfig,
    ParserConfig,
    ServiceConfig,
)

CONFIG_FILES = {
    "parser": "config_parser.yaml",
    "dom": "config_dom.yaml",
    "filters": "config_filters.yaml",
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
    filters_data = _load_yaml(base / CONFIG_FILES["filters"])
    service_data = _load_yaml(base / CONFIG_FILES["service"])
    logging_data = _load_yaml(base / CONFIG_FILES["logging"])

    service_model = ServiceConfig.model_validate(service_data)

    # Переопределение через переменные окружения (для Docker/CI).
    env_dsn = os.environ.get("ZAKUPKI_DB_DSN")
    if env_dsn:
        service_model.db.dsn = env_dsn

    return AppConfig(
        configs_dir=base,
        parser=ParserConfig.model_validate(parser_data),
        dom=DomConfig.model_validate(dom_data),
        filters=FiltersConfig.model_validate(filters_data),
        service=service_model,
        logging=LoggingConfig.model_validate(logging_data),
    )
