"""Unit-тесты загрузки и валидации конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig, SortConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"


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
    assert platform.list_config.container
    assert platform.list_config.detail_link
    assert platform.detail.variables is not None


def test_platform_sort_order_fixed(app_config: AppConfig) -> None:
    sort = app_config.dom.platforms["zakupki_mos"].sort
    assert sort is not None
    assert sort.order == "publication_date_desc"


def test_sort_order_other_value_rejected() -> None:
    with pytest.raises(ValidationError):
        SortConfig.model_validate({"order": "relevance"})


def test_sort_default_order() -> None:
    assert SortConfig().order == "publication_date_desc"


def test_telegram_token_injected_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZAKUPKI_TELEGRAM_TOKEN", "123:ABC")
    cfg = load_config(CONFIGS_DIR)
    assert cfg.service.notifications.telegram.token == "123:ABC"


def test_notifications_default_backend_is_webhook(app_config: AppConfig) -> None:
    assert app_config.service.notifications.backend == "webhook"
    assert app_config.service.notifications.telegram.enabled is False
