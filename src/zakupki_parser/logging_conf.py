"""Настройка логирования по конфигу ``config_log.yaml``."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from zakupki_parser.config.models import LoggingConfig


def setup_logging(cfg: LoggingConfig) -> None:
    """Конфигурирует корневой логгер согласно ``cfg``."""
    root = logging.getLogger()
    root.setLevel(cfg.level.upper())
    root.handlers.clear()

    fmt = logging.Formatter(cfg.format)

    if cfg.console:
        console = logging.StreamHandler()
        console.setLevel(cfg.level.upper())
        console.setFormatter(fmt)
        root.addHandler(console)

    if cfg.file:
        file_handler = RotatingFileHandler(
            cfg.file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(cfg.file_level.upper())
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    # Уменьшаем шум от сторонних библиотек
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
