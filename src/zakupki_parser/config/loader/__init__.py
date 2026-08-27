"""Загрузка и валидация YAML-конфигов.

Чтение файлов вынесено в ``yaml``, инъекция секретов/переопределений из env —
в ``env``. Здесь — публичный ``load_config`` и реэкспорт для совместимости с
прежним модулем ``config/loader.py``.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from zakupki_parser.config.loader.env import _apply_auth_env, _apply_env_overrides, _inject_chat_ids
from zakupki_parser.config.loader.yaml import (
    CONFIG_FILES,
    DOM_CONFIGS_DIR,
    _load_dom_configs,
    _load_yaml,
)
from zakupki_parser.config.models import (
    AppConfig,
    DomConfig,
    LoggingConfig,
    OpsConfig,
    ParserConfig,
    ScoreConfig,
    ScoringOpsConfig,
    ServiceConfig,
)

__all__ = [
    "CONFIG_FILES",
    "DOM_CONFIGS_DIR",
    "load_config",
    "_load_dom_configs",
]


def load_config(configs_dir: str | Path) -> AppConfig:
    """Загружает все конфиги из ``configs_dir`` и возвращает ``AppConfig``."""
    base = Path(configs_dir).expanduser().resolve()

    # Секреты из .env в корне проекта (переменные окружения приоритетнее).
    load_dotenv(base.parent / ".env")

    parser_data = _load_yaml(base / CONFIG_FILES["parser"])
    dom_data = _load_dom_configs(base)
    service_data = _load_yaml(base / CONFIG_FILES["service"])
    ops_data = _load_yaml(base / CONFIG_FILES["ops"])
    logging_data = _load_yaml(base / CONFIG_FILES["logging"])
    score_data = _load_yaml(base / CONFIG_FILES["score"])
    scoring_ops_data = _load_yaml(base / CONFIG_FILES["scoring_ops"])

    # chat_id каналов можно задать из env (как и токены) — подставляем ДО
    # валидации, т.к. включённый бэкенд без chat_id — ошибка конфигурации.
    # Уведомления относятся к эксплуатационному (devops) конфигу.
    _inject_chat_ids(ops_data)

    # Параметры авторизации из env (секрет подписи, внутренний токен) — ДО
    # валидации: без секрета конфигурация невалидна (auth всегда включён, fail fast).
    _apply_auth_env(ops_data)

    service_model = ServiceConfig.model_validate(service_data)
    logging_model = LoggingConfig.model_validate(logging_data)
    dom_model = DomConfig.model_validate(dom_data)
    score_model = ScoreConfig.model_validate(score_data)
    scoring_ops_model = ScoringOpsConfig.model_validate(scoring_ops_data)
    ops_model = OpsConfig.model_validate(ops_data)

    # Относительный путь файла лога — относительно корня проекта (родителя configs).
    if logging_model.file:
        log_path = Path(logging_model.file)
        if not log_path.is_absolute():
            log_path = base.parent / log_path
        logging_model.file = str(log_path)

    # Относительный путь маппинга ОКПД2 — относительно корня проекта.
    for platform in dom_model.platforms.values():
        if platform.search and platform.search.okpd_tree_file:
            tree_path = Path(platform.search.okpd_tree_file)
            if not tree_path.is_absolute():
                tree_path = base.parent / tree_path
            platform.search.okpd_tree_file = str(tree_path)

    _apply_env_overrides(parser_data, ops_model, score_model, base)

    return AppConfig(
        configs_dir=base,
        parser=ParserConfig.model_validate(parser_data),
        dom=dom_model,
        service=service_model,
        ops=ops_model,
        logging=logging_model,
        score=score_model,
        scoring_ops=scoring_ops_model,
    )
