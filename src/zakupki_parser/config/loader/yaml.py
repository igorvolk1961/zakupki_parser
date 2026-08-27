"""Загрузка YAML-конфигов: единичный файл и каталог площадок ``dom``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_FILES = {
    "parser": "config_parser.yaml",
    "service": "config_service.yaml",
    "ops": "config_ops.yaml",
    "logging": "config_log.yaml",
    "score": "config_score.yaml",
    "scoring_ops": "config_score_ops.yaml",
}

# Каталог с конфигами площадок (по одному YAML на площадку; имя файла = platform_id).
DOM_CONFIGS_DIR = "dom"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Конфиг-файл не найден: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Конфиг {path} должен быть YAML-словарём")
    return data


def _load_dom_configs(base: Path) -> dict[str, Any]:
    """Собирает ``platforms`` из ``configs/dom/*.yaml``.

    Каждый файл в подкаталоге ``dom`` описывает одну площадку: имя файла (без
    расширения) — ``platform_id``, содержимое — сам блок площадки. Для обратной
    совместимости (тестовый набор ``tests/configs``, имитатор) при отсутствии
    подкаталога читается единый ``config_dom.yaml`` с ключом ``platforms``.
    """
    dom_dir = base / DOM_CONFIGS_DIR
    if dom_dir.is_dir():
        platforms: dict[str, Any] = {}
        for path in sorted(dom_dir.glob("*.yaml")):
            platform_id = path.stem
            data = _load_yaml(path)
            if platform_id in platforms:
                raise ValueError(f"Дубликат platform_id в configs/{DOM_CONFIGS_DIR}: {path}")
            platforms[platform_id] = data
        return {"platforms": platforms}

    legacy = base / "config_dom.yaml"
    if not legacy.is_file():
        raise FileNotFoundError(
            f"Нет конфигов площадок: нет каталога configs/{DOM_CONFIGS_DIR} и файла config_dom.yaml"
        )
    data = _load_yaml(legacy)
    if "platforms" not in data:
        raise ValueError("config_dom.yaml должен содержать ключ platforms")
    return data
