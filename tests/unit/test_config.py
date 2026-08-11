"""Unit-тесты загрузки и валидации конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig, NotificationsConfig, ServiceConfig, SortConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "tests" / "configs"


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


def test_chat_id_injected_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_id канала можно задать из env (до валидации), как токены."""
    monkeypatch.setenv("ZAKUPKI_MAX_CHAT_ID", "111111111")
    cfg = load_config(CONFIGS_DIR)
    assert cfg.service.notifications.max.chat_id == "111111111"


def test_chat_id_in_env_satisfies_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включённый бэкенд max без chat_id в YAML валиден, если chat_id из env."""
    monkeypatch.setenv("ZAKUPKI_MAX_CHAT_ID", "111111111")
    cfg = load_config(CONFIGS_DIR)
    n = cfg.service.notifications
    # В конфиге backend=max, max.enabled=true, chat_id: null в YAML.
    assert n.backend == "max"
    assert n.max.enabled is True
    assert n.max.chat_id == "111111111"


def test_notifications_default_backend_is_webhook() -> None:
    cfg = NotificationsConfig()
    assert cfg.backend == "webhook"
    assert cfg.telegram.enabled is False
    assert cfg.webhook.enabled is False


def test_service_config_rejects_unknown_keys() -> None:
    """Опечатки в ключах конфига не должны молча игнорироваться (extra='forbid')."""
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate({"timeout_second": 100})


def test_service_config_rejects_unknown_nested_keys() -> None:
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate(
            {"notifications": {"backend": "webhook", "telegram": {"chatd": "x"}}}
        )
