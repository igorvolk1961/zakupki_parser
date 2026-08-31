"""Unit-тесты загрузки и валидации конфигурации."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from zakupki_parser.config.loader import _load_dom_configs, load_config
from zakupki_parser.config.models import (
    AppConfig,
    DomConfig,
    NotificationsConfig,
    ServiceConfig,
    SortConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "tests" / "configs"


def test_load_config_ok(app_config: AppConfig) -> None:
    assert app_config.dom.platforms
    assert "zakupki_mos" in app_config.dom.platforms
    assert app_config.parser.browser.delay_between_actions_seconds == (4.0, 12.0)


def test_stop_conditions_defaults(app_config: AppConfig) -> None:
    sc = app_config.service.search_criteria
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


def test_roseltorg_number_card_regex_both_formats() -> None:
    """Номер из карточки: старый формат (COM…), новый (SP…) и просто цифры без префикса."""
    data = _load_dom_configs(REPO_ROOT / "configs")
    platforms = DomConfig.model_validate(data).platforms
    for platform_id in ("roseltorg_223fz", "roseltorg_44fz"):
        platform = platforms[platform_id]
        number_var = next(v for v in platform.list_config.variables if v.name == "number")
        arg = number_var.handler_arg
        assert arg == r"(COM\d{10,}|SP\d{8,}|\d{10,})", platform_id
        # 223-ФЗ/коммерческие: 11 цифр, COM- и SP-префиксы; 44-ФЗ: 19 цифр.
        m1 = re.search(arg, "32616082276\n(Лот 1)")
        assert m1 is not None
        assert m1.group(1) == "32616082276"
        m2 = re.search(arg, "COM14082600147 (Лот 1)")
        assert m2 is not None
        assert m2.group(1) == "COM14082600147"
        m3 = re.search(arg, "0373200022226001723\n(Лот 1)")
        assert m3 is not None
        assert m3.group(1) == "0373200022226001723"
        m4 = re.search(arg, "SP10212041 (Лот 1)")
        assert m4 is not None
        assert m4.group(1) == "SP10212041"


def test_roseltorg_44fz_config_complete() -> None:
    """roseltorg_44fz — рабочий конфиг: поиск через /procedures/search + place=44fz."""
    data = _load_dom_configs(REPO_ROOT / "configs")
    platform = DomConfig.model_validate(data).platforms["roseltorg_44fz"]
    assert platform.list_config.container == "div.search-results__item"
    assert platform.list_config.variables, "переменные списка не заданы"
    # /search/44fz — лендинг без фильтрации; поиск идёт через /procedures/search с place=44fz.
    assert platform.list_path == "/procedures/search"
    assert platform.search is not None
    assert platform.search.query_params.get("place") == "44fz"
    assert platform.search.criteria_map["okpd2"].raw_array_flat == "okpd2[]"


def test_roseltorg_search_status_all() -> None:
    """Поиск roseltorg покрывает все статусы (0-5) через state_ids.all (active_only)."""
    data = _load_dom_configs(REPO_ROOT / "configs")
    platforms = DomConfig.model_validate(data).platforms
    for platform_id in ("roseltorg_223fz", "roseltorg_44fz"):
        platform = platforms[platform_id]
        assert platform.search is not None
        # Статусы больше не статичные: all/active заданы в state_ids для active_only.
        assert "status[]" not in platform.search.query_params
        assert platform.search.state_ids == {
            "all": [5, 0, 1, 2, 3, 4],
            "active": [5, 0, 1],
        }, platform_id
        active_only = platform.search.criteria_map["active_only"]
        assert active_only.raw_array_flat == "status[]"


def test_all_platforms_have_purchase_type() -> None:
    """Каждая площадка извлекает тип процедуры (purchase_type) из карточки списка."""
    data = _load_dom_configs(REPO_ROOT / "configs")
    platforms = DomConfig.model_validate(data).platforms
    for platform_id, platform in platforms.items():
        names = [v.name for v in platform.list_config.variables]
        assert "purchase_type" in names, f"{platform_id}: не задана переменная purchase_type"


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
            "auth": {"secret": "test-secret", "internal_token": "internal-123"},
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


def test_service_config_scoring_defaults(app_config: AppConfig) -> None:
    """Аналитические скор-настройки валидируются и имеют дефолты."""
    sc = app_config.service.scoring
    assert sc.embedding_filter_threshold >= 0
    assert sc.max_fit_score > sc.min_fit_score
    assert sc.score_round_digits >= 0
    assert sc.num_refine_rounds >= 0


def test_service_config_scoring_loaded_from_seed(app_config: AppConfig) -> None:
    """Значения из config_service.yaml -> scoring подхватываются загрузчиком."""
    scoring = app_config.service.scoring
    assert scoring.embedding_filter_threshold == 0.55
    # Технические флаги (нормализация Fit, уточнение по ТЗ) не настраиваются аналитиком.
    assert not hasattr(scoring, "normalize_fit_for_score")
    assert not hasattr(scoring, "tz_review_enabled")
    # Обязательная нормализация Fit действует на уровне scoring_service.
    assert scoring.tz_download_timeout > 0


def test_score_service_config_rejects_unknown_keys() -> None:
    from zakupki_parser.config.models import ScoringServiceConfig

    with pytest.raises(ValidationError):
        ScoringServiceConfig.model_validate({"llm_model_typo": "x"})
    # Неизвестный ключ в модели-образце scoring_service — ошибка (reject опечаток).
    with pytest.raises(ValidationError):
        ScoringServiceConfig.model_validate({"scoring": {"filter_threshold_typo": 0.5}})


def test_service_config_scoring_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate({"scoring": {"filter_threshold_typo": 0.5}})


def test_service_schema_includes_scoring() -> None:
    """Форма «Параметры мониторинга» (аналитик) содержит блок правил оценки."""
    from zakupki_parser.api.app.config_schema import build_schema

    schema = build_schema(ServiceConfig)
    scoring = next(f for f in schema if f["key"] == "scoring")
    assert scoring["kind"] == "object"
    keys = {sub["key"] for sub in scoring["fields"]}
    assert {"embedding_filter_threshold", "giga_embedding_alpha", "num_refine_rounds"} <= keys


def test_score_service_schema_has_no_secrets_and_expected_fields() -> None:
    """Форма «Скоринг-сервис» (devops): несекретная конфигурация без секретов."""
    from zakupki_parser.api.app.config_schema import build_schema
    from zakupki_parser.config.models import ScoringServiceConfig

    schema = build_schema(ScoringServiceConfig)
    keys = {f["key"] for f in schema}
    assert {
        "llm_base_url",
        "llm_model",
        "giga_base_url",
        "embedding_filter_threshold",
        "score_round_digits",
    } <= keys
    # Секреты не выводятся в форму (управляются через .env сервиса).
    assert not (
        keys
        & {
            "llm_api_key",
            "giga_client_id",
            "giga_client_secret",
            "auth_token",
            "parser_internal_token",
        }
    )
