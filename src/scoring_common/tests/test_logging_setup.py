"""Regression: file_level="DEBUG" должен реально попадать в файл лога.

Баг: ``setup_logging`` ставил root-логгеру ``level`` ("INFO"), что отсекает
запись ДО того, как её увидит хоть один handler — file_level="DEBUG" файлового
handler'а был мёртвым кодом: ни один ``logger.debug(...)`` в приложении не
доходил до файла, несмотря на видимость обратного (httpx-запросы попадали в
файл, но они эмитятся на INFO и понижаются до DEBUG уже ПОСЛЕ прохождения
root-фильтра — см. ``_HttpxRequestDowngradeFilter``, особый случай, а не общий
механизм).
"""

from __future__ import annotations

import logging

from scoring_common.logging import LoggingSettings, setup_logging


def test_debug_reaches_file_when_console_level_is_info(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "test.log"
    cfg = LoggingSettings(level="INFO", file="test.log", file_level="DEBUG", console=True)
    try:
        setup_logging(cfg)
        logging.getLogger("some.module").debug("тестовое debug-сообщение")
        for handler in logging.getLogger().handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "тестовое debug-сообщение" in content
    finally:
        logging.getLogger().handlers.clear()


def test_debug_does_not_reach_console_when_console_level_is_info(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = LoggingSettings(level="INFO", file="test.log", file_level="DEBUG", console=True)
    try:
        setup_logging(cfg)
        logging.getLogger("some.module").debug("не должно быть в консоли")
        captured = capsys.readouterr()
        assert "не должно быть в консоли" not in captured.err
        assert "не должно быть в консоли" not in captured.out
    finally:
        logging.getLogger().handlers.clear()


def test_root_level_matches_console_when_file_disabled(tmp_path) -> None:
    cfg = LoggingSettings(level="INFO", file=None, console=True)
    try:
        setup_logging(cfg)
        assert logging.getLogger().level == logging.INFO
    finally:
        logging.getLogger().handlers.clear()
