"""Работа с деревом ОКПД2 площадки: маппинг «код → путь».

Пути в ``needSpecificFilter.okpdPaths`` (например, ``.1147303.1133182.``) — это
внутренние ID узлов дерева площадки, а не коды ОКПД2. Соответствие код→путь
берётся из дерева площадки (снимок разметки выбранных ветвей ОКПД2 или
автоматический обход). Здесь — парсинг снимка, загрузка маппинга и резолв
человекочитаемых кодов в пути.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Метка узла: <a class="ui label" value=".path.">Название (код)<i .../></a>
_LABEL_RE = re.compile(r'value="(\.[0-9]+(?:\.[0-9]+)*\.)"[^>]*>([^<]+?)\((\d+(?:\.\d+)*)\)<')


def parse_tree_html(html: str) -> dict[str, Any]:
    """Разбирает снимок выбранных ветвей ОКПД2 в маппинг код→путь и путь→имя."""
    code_to_path: dict[str, str] = {}
    path_to_name: dict[str, str] = {}
    for path, name, code in _LABEL_RE.findall(html):
        name = name.strip()
        code_to_path[code] = path
        path_to_name[path] = name
    return {
        "code_to_path": code_to_path,
        "path_to_name": path_to_name,
    }


def load_okpd_tree(path: str | Path) -> dict[str, Any]:
    """Загружает маппинг дерева ОКПД2 из JSON-файла."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Маппинг ОКПД2 {path} должен быть JSON-объектом")
    return data


def resolve_okpd_codes(
    codes: list[str], tree: dict[str, Any], *, warn_missing: bool = True
) -> list[str]:
    """Преобразует коды ОКПД2 в пути узлов дерева площадки.

    Возвращает список путей для ``okpdPaths``. Коды, отсутствующие в маппинге,
    пропускаются (с предупреждением) — парсинг не ломается.
    """
    code_to_path = tree.get("code_to_path", {})
    paths: list[str] = []
    for code in codes:
        path = code_to_path.get(code)
        if path:
            paths.append(path)
        elif warn_missing:
            logger.warning("Код ОКПД2 %s не найден в дереве, пропущен", code)
    return paths
