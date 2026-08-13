"""Unit-тесты загрузки и валидации конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zakupki_parser.config.loader import _load_dom_configs, load_config
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


def test_load_dom_configs_from_dir(tmp_path: Path) -> None:
    (tmp_path / "dom").mkdir()
    (tmp_path / "dom" / "a.yaml").write_text("name: A\n", encoding="utf-8")
    (tmp_path / "dom" / "b.yaml").write_text("name: B\n", encoding="utf-8")
    data = _load_dom_configs(tmp_path)
    assert set(data["platforms"]) == {"a", "b"}
    assert data["platforms"]["a"]["name"] == "A"


def test_load_dom_configs_legacy_fallback(tmp_path: Path) -> None:
    (tmp_path / "config_dom.yaml").write_text(
        "platforms:\n  zakupki_mos:\n    name: X\n", encoding="utf-8"
    )
    data = _load_dom_configs(tmp_path)
    assert data["platforms"]["zakupki_mos"]["name"] == "X"


def test_load_dom_configs_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _load_dom_configs(tmp_path)


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
    assert cfg.ops.notifications.telegram.token == "123:ABC"


def test_chat_id_injected_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_id канала можно задать из env (до валидации), как токены."""
    monkeypatch.setenv("ZAKUPKI_MAX_CHAT_ID", "111111111")
    cfg = load_config(CONFIGS_DIR)
    assert cfg.ops.notifications.max.chat_id == "111111111"


def test_chat_id_in_env_satisfies_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включённый бэкенд max без chat_id в YAML валиден, если chat_id из env."""
    monkeypatch.setenv("ZAKUPKI_MAX_CHAT_ID", "111111111")
    cfg = load_config(CONFIGS_DIR)
    n = cfg.ops.notifications
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
        ServiceConfig.model_validate({"default_cutoff_day": 100})


def test_service_config_rejects_ops_keys() -> None:
    """Эксплуатационные ключи (таймер, БД, уведомления) не входят в аналитический
    ServiceConfig — они переехали в OpsConfig (config_ops.yaml)."""
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate({"timeout_seconds": 3600})


def test_ops_config_accepts_devops_keys() -> None:
    """Devops-параметры валидируются через OpsConfig."""
    from zakupki_parser.config.models import OpsConfig

    ops = OpsConfig.model_validate(
        {
            "timeout_seconds": 7200,
            "db": {"dsn": "postgresql+asyncpg://u:p@h:5432/db", "enabled": True},
            "notifications": {"backend": "none"},
            "export_dir": "/tmp/export",
            "circuit_breaker_failure_threshold": 3,
            "circuit_breaker_reset_timeout_seconds": 30.0,
        }
    )
    assert ops.timeout_seconds == 7200
    assert ops.db.enabled is True
    assert ops.notifications.backend == "none"
    assert ops.circuit_breaker_failure_threshold == 3


def test_ops_config_rejects_unknown_keys() -> None:
    from zakupki_parser.config.models import OpsConfig

    with pytest.raises(ValidationError):
        OpsConfig.model_validate({"timeout_second": 100})


def test_service_config_rejects_unknown_nested_keys() -> None:
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate({"search_criteria": {"okpd_cod": "x"}})


def test_ops_config_rejects_unknown_nested_keys() -> None:
    from zakupki_parser.config.models import OpsConfig

    with pytest.raises(ValidationError):
        OpsConfig.model_validate(
            {"notifications": {"backend": "webhook", "telegram": {"chatd": "x"}}}
        )
