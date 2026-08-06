"""Настройка логирования по конфигу ``config_log.yaml``."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from zakupki_parser.config.models import LoggingConfig


class _NameRewriteFilter(logging.Filter):
    """Переименовывает служебные логгеры uvicorn в нейтральные.

    ``uvicorn.error`` логирует и INFO-сообщения — слово «error» в имени логгера
    пугает заказчика. Приводим к читаемым именам: uvicorn.error -> uvicorn,
    uvicorn.access -> http.
    """

    _REWRITE = {
        "uvicorn.error": "uvicorn",
        "uvicorn.access": "http",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        for prefix, new_name in self._REWRITE.items():
            if record.name == prefix or record.name.startswith(prefix + "."):
                record.name = new_name
                break
        return True


def setup_logging(cfg: LoggingConfig) -> None:
    """Конфигурирует корневой логгер согласно ``cfg``."""
    root = logging.getLogger()
    root.setLevel(cfg.level.upper())
    root.handlers.clear()

    fmt = logging.Formatter(cfg.format)
    name_filter = _NameRewriteFilter()

    if cfg.console:
        console = logging.StreamHandler()
        console.setLevel(cfg.level.upper())
        console.setFormatter(fmt)
        console.addFilter(name_filter)
        root.addHandler(console)

    if cfg.file:
        file_path = Path(cfg.file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # При truncate_on_start=True файл очищается при старте (иначе — дописываем).
        if cfg.truncate_on_start:
            file_path.write_text("", encoding="utf-8")
        file_handler = RotatingFileHandler(
            file_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(cfg.file_level.upper())
        file_handler.setFormatter(fmt)
        file_handler.addFilter(name_filter)
        root.addHandler(file_handler)

    # Уменьшаем шум от сторонних библиотек
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    # uvicorn.access не должен фильтроваться ниже INFO (иначе нет лога запросов).
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
