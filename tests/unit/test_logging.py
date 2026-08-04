"""Unit-тесты настройки логирования (файл, флаг очистки)."""

from __future__ import annotations

import logging
from pathlib import Path

from zakupki_parser.config.models import LoggingConfig
from zakupki_parser.logging_conf import setup_logging


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_setup_writes_file(tmp_path: Path) -> None:
    log_file = tmp_path / "parser.log"
    cfg = LoggingConfig(level="INFO", file=str(log_file), file_level="DEBUG", console=False)
    setup_logging(cfg)
    logging.getLogger("test").info("hello log")
    _flush()
    assert log_file.is_file()
    assert "hello log" in log_file.read_text(encoding="utf-8")


def test_truncate_on_start(tmp_path: Path) -> None:
    log_file = tmp_path / "parser.log"
    log_file.write_text("old content\n", encoding="utf-8")
    cfg = LoggingConfig(
        level="INFO",
        file=str(log_file),
        file_level="DEBUG",
        console=False,
        truncate_on_start=True,
    )
    setup_logging(cfg)
    assert "old content" not in log_file.read_text(encoding="utf-8")


def test_append_without_truncate(tmp_path: Path) -> None:
    log_file = tmp_path / "parser.log"
    log_file.write_text("old content\n", encoding="utf-8")
    cfg = LoggingConfig(level="INFO", file=str(log_file), file_level="DEBUG", console=False)
    setup_logging(cfg)
    assert "old content" in log_file.read_text(encoding="utf-8")
