"""Unit-тесты логирования (переименование служебных логгеров uvicorn)."""

from __future__ import annotations

import logging

from zakupki_parser.logging_conf import _NameRewriteFilter


def _record(name: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=logging.INFO, pathname=__file__, lineno=1, msg="m", args=(), exc_info=None
    )


def test_uvicorn_error_renamed_to_uvicorn() -> None:
    rec = _record("uvicorn.error")
    assert _NameRewriteFilter().filter(rec) is True
    assert rec.name == "uvicorn"


def test_uvicorn_access_renamed_to_http() -> None:
    rec = _record("uvicorn.access")
    assert _NameRewriteFilter().filter(rec) is True
    assert rec.name == "http"


def test_other_loggers_untouched() -> None:
    for name in ("zakupki_parser.parser", "root", "playwright"):
        rec = _record(name)
        assert _NameRewriteFilter().filter(rec) is True
        assert rec.name == name
