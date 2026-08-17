"""Unit-тесты CLI: информативный вывод check-config."""

from __future__ import annotations

import contextlib
import io

from zakupki_parser.cli import _print_summary
from zakupki_parser.config.models import AppConfig


def test_check_config_summary(app_config: AppConfig) -> None:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_summary(app_config)
    out = buf.getvalue()

    # Заголовок валидности и разделы.
    assert "Конфигурация валидна" in out
    assert "Сервис (config_service.yaml)" in out
    assert "Скоринг (config_score.yaml)" in out
    assert "Уведомления" in out
    assert "БД" in out
    assert "Парсер / браузер" in out

    # Файлы конфигурации.
    assert "config_service.yaml" in out
    assert "config_dom.yaml" in out

    # Ключевые параметры из config_service.yaml.
    assert "Площадок в списке сайтов" in out
    assert "zakupki_mos" in out
    assert "Критерии поиска" in out
    assert "ключевые слова" in out
    assert "состояние=" in out
    assert "Порог дат (дней):" in out
    assert "режим:" in out

    # Скоринг.
    assert "P(win):" in out
    assert "fit-таблица" in out

    # Парсер.
    assert "Задержки между действиями" in out
    assert "Persistent session" in out


def test_check_config_masks_dsn(app_config: AppConfig) -> None:
    """В выводе не должен присутствовать пароль из DSN."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_summary(app_config)
    out = buf.getvalue()
    assert "Подключение: " in out

    dsn = app_config.ops.db.dsn
    userinfo = dsn.split("://")[1].split("@")[0]
    if ":" in userinfo:
        password = userinfo.split(":", 1)[1]
        # В маскированной строке не должно быть вида "user:password@".
        dsn_line = next(line for line in out.splitlines() if "Подключение:" in line)
        assert f":{password}@" not in dsn_line
