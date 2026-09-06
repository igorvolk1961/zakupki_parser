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
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Метка узла: <a class="ui label" value=".path.">Название (код)<i .../></a>
_LABEL_RE = re.compile(r'value="(\.[0-9]+(?:\.[0-9]+)*\.)"[^>]*>([^<]+?)\((\d+(?:\.\d+)*)\)<')

# Код ОКПД2: 2-9 цифр, разделённые точками; класс всегда 2 цифры, вложенные
# блоки — 1-3 цифры (напр. 62, 62.02, 62.02.1, 62.02.20.110).
_OKPD2_BLOCK = r"\d{1,3}"
_OKPD2_RE = re.compile(rf"^\d{{2}}(?:\.{_OKPD2_BLOCK})*$")


def normalize_okpd_code(raw: str) -> str:
    """Нормализует и проверяет один код ОКПД2 (формат: цифры и точки).

    Принимает код с разделителями ``.``/``-``/пробелами либо «голые» цифры
    (2-9). Возвращает канонический текст с точками; бросает ``ValueError`` для
    не-кода (буквы, лишние разделители, неверная длина/структура).
    """
    if not raw or not raw.strip():
        raise ValueError("Код ОКПД2 пуст")
    # Пробелы и дефисы — разделители групп (62 02 / 62-02 → 62.02).
    cleaned = re.sub(r"\s+", ".", raw.strip()).replace("-", ".")
    # «Голые» цифры: оставляем как введено (площадки сами резолвят по цифрам).
    if cleaned.isdigit():
        if not 2 <= len(cleaned) <= 9:
            raise ValueError(f"Код ОКПД2 «{raw}» должен содержать от 2 до 9 цифр")
        return cleaned
    if not _OKPD2_RE.match(cleaned):
        raise ValueError(
            f"Код ОКПД2 «{raw}» имеет неверный формат: цифры, разделённые точками "
            "(например, 62.02 или 62.02.20.110)"
        )
    digits = re.sub(r"\D", "", cleaned)
    if not 2 <= len(digits) <= 9:
        raise ValueError(f"Код ОКПД2 «{raw}» должен содержать от 2 до 9 цифр")
    return cleaned


def normalize_okpd_codes(codes: Sequence[str | None] | None) -> list[str]:
    """Нормализует список кодов ОКПД2: проверка формата, трим, дедупликация."""
    if not codes:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in codes:
        if raw is None:
            continue
        code = normalize_okpd_code(str(raw))
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


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


def _digits(code: str) -> str:
    """Приводит код к цифровой строке (убирает точки/дефисы/пробелы)."""
    return re.sub(r"\D", "", code)


def _append_unique(paths: list[str], path: str) -> None:
    if path not in paths:
        paths.append(path)


def resolve_codes_to_paths(
    codes: list[str],
    tree: dict[str, Any],
    *,
    label: str = "ОКПД2",
    warn_missing: bool = True,
) -> list[str]:
    """Преобразует коды любой вложенности в пути узлов дерева площадки.

    Используется и для ОКПД2, и для регионов (формат маппинга одинаков —
    ``code_to_path``). Пользователь может указать любой известный ему код, не зная
    состава маппинга. Резолв по приоритету:
      1) точный код есть в маппинге — его путь;
      2) точного кода нет, но есть его ПОТОМКИ — объединяем пути всех потомков
         (точно покрывает запрошенную ветвь, напр. «62.02» = 62.02.1+62.02.2+…);
      3) иначе — путь ближайшего ПРЕДКА из маппинга (его путь включает потомков).
    Ввод нормализуется (точки/дефисы/пробелы не важны). Результат дедуплицируется;
    код без предка/потомков пропускается с предупреждением.
    """
    code_to_path = tree.get("code_to_path", {})
    # индекс: цифровая строка кода -> (исходный код, путь)
    index = {_digits(key): (key, path) for key, path in code_to_path.items()}
    paths: list[str] = []
    for code in codes:
        digits = _digits(code)
        if not digits:
            continue

        # 1) точный код
        exact = index.get(digits)
        if exact is not None:
            _append_unique(paths, exact[1])
            continue

        # 2) потомки (объединение путей всех узлов с данным префиксом)
        descendants = sorted(
            (key, path)
            for key_digits, (key, path) in index.items()
            if len(key_digits) > len(digits) and key_digits.startswith(digits)
        )
        if descendants:
            for _, path in descendants:
                _append_unique(paths, path)
            logger.info(
                "%s %s: нет точного кода, объединяем потомков: %s",
                label,
                code,
                [key for key, _ in descendants],
            )
            continue

        # 3) ближайший предок
        best_len = 0
        best_key: str | None = None
        best_path: str | None = None
        for key_digits, (key, path) in index.items():
            if digits.startswith(key_digits) and len(key_digits) > best_len:
                best_len = len(key_digits)
                best_key = key
                best_path = path
        if best_path is not None:
            _append_unique(paths, best_path)
            logger.info("%s %s: используем ближайшего предка %s", label, code, best_key)
        elif warn_missing:
            logger.warning("Код %s %s не имеет предка/потомков в маппинге, пропущен", label, code)
    return paths


def resolve_okpd_codes(
    codes: list[str], tree: dict[str, Any], *, warn_missing: bool = True
) -> list[str]:
    """Преобразует коды ОКПД2 любой вложенности в пути узлов дерева площадки."""
    return resolve_codes_to_paths(codes, tree, label="ОКПД2", warn_missing=warn_missing)
