"""Unit-тесты генерации схем конфигов для веб-форм (config_schema.py)."""

from __future__ import annotations

from typing import Any

from zakupki_parser.api.app.config_schema import build_schema
from zakupki_parser.config.models import LoggingConfig, OpsConfig, ParserConfig, ServiceConfig


def _find(fields: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next(f for f in fields if f["key"] == key)


def test_service_schema_kinds() -> None:
    schema = build_schema(ServiceConfig)
    sites = _find(schema, "sites")
    assert sites["kind"] == "list"
    # Строка списка sites — модель SiteServiceEntry: platform_id + enabled.
    item_keys = {f["key"] for f in sites["item"]}
    assert item_keys == {"platform_id", "enabled"}
    assert _find(sites["item"], "enabled")["kind"] == "bool"

    sc = _find(schema, "search_criteria")
    assert sc["kind"] == "object"
    assert _find(sc["fields"], "active_only")["kind"] == "bool"

    assert _find(schema, "default_cutoff_days")["kind"] == "int"
    assert _find(schema, "sort_by_date_only")["kind"] == "bool"

    loop = _find(schema, "profiles_loop_order")
    assert loop["kind"] == "select"
    assert set(loop["options"]) == {"platform_then_profile", "profile_then_platform"}
    assert _find(schema, "deduplicate_requests")["kind"] == "bool"


def test_service_schema_platform_options() -> None:
    schema = build_schema(
        ServiceConfig, options_overrides={"sites.platform_id": ["etpgpb", "zakupki_mos"]}
    )
    sites = _find(schema, "sites")
    platform = _find(sites["item"], "platform_id")
    assert platform["kind"] == "select"
    assert set(platform["options"]) == {"etpgpb", "zakupki_mos"}


def test_ops_schema_no_secrets() -> None:
    schema = build_schema(OpsConfig)
    keys = {f["key"] for f in schema}
    assert {"auth", "db", "notifications", "export_dir", "prompts_dir"} <= keys
    auth = _find(schema, "auth")
    auth_keys = {f["key"] for f in auth["fields"]}
    # Секреты (secret/internal_token) в форму не выводятся — только env.
    assert auth_keys == {"enabled", "token_ttl_seconds"}

    notif = _find(schema, "notifications")
    notif_keys = {f["key"] for f in notif["fields"]}
    assert "backend" in notif_keys
    backend = _find(notif["fields"], "backend")
    assert backend["kind"] == "select"
    assert set(backend["options"]) == {"telegram", "webhook", "max", "none"}
    telegram = _find(notif["fields"], "telegram")
    telegram_keys = {f["key"] for f in telegram["fields"]}
    assert "token" not in telegram_keys


def test_log_schema() -> None:
    schema = build_schema(LoggingConfig)
    assert _find(schema, "console")["kind"] == "bool"
    assert _find(schema, "level")["kind"] == "str"
    assert _find(schema, "file")["kind"] == "str"


def test_parser_schema_readonly_fields() -> None:
    schema = build_schema(ParserConfig)
    browser = _find(schema, "browser")
    assert browser["kind"] == "object"
    delay = _find(browser["fields"], "delay_between_actions_seconds")
    assert delay["kind"] == "text"
    assert _find(schema, "max_list_pages")["kind"] == "int"
