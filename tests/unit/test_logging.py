"""Unit-тесты логирования (переименование служебных логгеров uvicorn)."""

from __future__ import annotations

import logging

from zakupki_parser.logging_conf import _NameRewriteFilter, _ScrubbingFormatter

_FORMAT = "%(message)s"


def _record(name: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=logging.INFO, pathname=__file__, lineno=1, msg="m", args=(), exc_info=None
    )


def _make_record(msg: str, args: tuple[object, ...] = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
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


def test_websocket_token_redacted_in_arg() -> None:
    rec = _make_record(
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:55200", "/ws?token=eyJzdWIiOjMsInJvbGVzIjpbImFuYWx5c3QiXQ"),
    )
    text = _ScrubbingFormatter(_FORMAT).format(rec)
    assert "token=eyJzdWIiOjMsInJvbGVzIjpbImFuYWx5c3QiXQ" not in text
    assert 'token=***"' in text
    assert "WebSocket /ws?token=***" in text


def test_token_redacted_in_direct_message() -> None:
    rec = _make_record("WebSocket /ws?token=abc123def")
    text = _ScrubbingFormatter(_FORMAT).format(rec)
    assert "abc123def" not in text
    assert "token=***" in text


def test_sensitive_params_redacted() -> None:
    rec = _make_record("/api?access_token=sekrit&key=123456&internal_token=abc")
    text = _ScrubbingFormatter(_FORMAT).format(rec)
    assert "sekrit" not in text
    assert "123456" not in text
    assert "abc" not in text
    assert "access_token=***" in text
    assert "key=***" in text
    assert "internal_token=***" in text


def test_plain_url_untouched() -> None:
    rec = _make_record("GET /health")
    text = _ScrubbingFormatter(_FORMAT).format(rec)
    assert text == "GET /health"


def test_multiple_tokens_in_one_line_redacted() -> None:
    rec = _make_record("/ws?token=one /ws?token=two")
    text = _ScrubbingFormatter(_FORMAT).format(rec)
    assert "one" not in text
    assert "two" not in text
    assert text.count("token=***") == 2
