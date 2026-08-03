"""Unit-тесты загрузки и валидации конфигурации."""

from __future__ import annotations

from zakupki_parser.config.models import AppConfig


def test_load_config_ok(app_config: AppConfig) -> None:
    assert app_config.dom.platforms
    assert "zakupki_mos" in app_config.dom.platforms
    assert app_config.parser.browser.delay_between_actions_seconds == (4.0, 12.0)


def test_stop_conditions_defaults(app_config: AppConfig) -> None:
    sc = app_config.service.stop_conditions
    assert sc.enabled is True
    assert sc.deadline_not_expired is True


def test_platform_has_list_and_detail(app_config: AppConfig) -> None:
    platform = app_config.dom.platforms["zakupki_mos"]
    assert platform.list.container
    assert platform.list.detail_link
    assert platform.detail.variables is not None
