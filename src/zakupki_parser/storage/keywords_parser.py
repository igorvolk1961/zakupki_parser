"""Разбор файла ключевых слов/компетенций профиля (сид, R8).

Формат — markdown с секциями ``**Заголовок**``. Поддерживаются имена секций в
двух вариантах (русском и в стиле агрегаторов ТендерПлан/ТендерЛэнд):
- ``name`` — имя профиля (например ``bbk-it``);
- ``Ключевые слова`` / ``keywords`` — позитивные выражения (type=keyword);
- ``Минус слова`` / ``exclussion_words`` / ``exclusion_words`` — слова-исключения
  (type=exclusion);
- ``Компетенции`` / ``competencies`` — текст компетенций (блок) ЛИБО путь к файлу
  с текстом компетенций (например в ``docs/references/``) — в этом случае
  содержимое файла подставляется при разборе ``parse_keywords_file`` или
  через ``resolve_competencies_reference`` (web-импорт);
- ``okpd_codes`` — коды ОКПД2 через запятую (критерий поиска профиля);
- ``nmck_min`` / ``nmck_max`` — диапазон НМЦК (число).

Каждая секция слов — список выражений через запятую. Допустимые формы:
- ``слов*`` — слово с усечением;
- ``(фраза* фраза*)~N`` — не более N слов между токенами (проксимити);
- ``точная фраза`` / ``"точная фраза"`` — фраза как есть.

Парсер нормализует выражения (снимает кавычки, обрезает пробелы) и сохраняет
исходный синтаксис ``*``/``~N`` — интерпретация происходит в фильтрации (Этап 3).
Слова попадают в таблицу ``keywords`` (канонический источник, ER: PROFILE -> KEYWORD);
критерии поиска — в колонки профиля (okpd_codes/nmck_min/nmck_max). Выбор по
состоянию (``active_only``) — глобальный, ``config_service.yaml``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SECTION_KEYWORDS = "Ключевые слова"
SECTION_EXCLUSIONS = "Минус слова"
SECTION_COMPETENCIES = "Компетенции"

# Канонический ключ -> имена секций (сравнение по заголовку, регистронезависимо).
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "keywords": (SECTION_KEYWORDS, "keywords"),
    "exclusion_words": (SECTION_EXCLUSIONS, "exclussion_words", "exclusion_words"),
    "competencies": (SECTION_COMPETENCIES, "competencies"),
    "okpd_codes": ("okpd_codes", "коды окпд2", "окпд2"),
    "nmck_min": ("nmck_min", "нмцк мин", "нмцк_мин"),
    "nmck_max": ("nmck_max", "нмцк макс", "нмцк_макс"),
}

_HEADING_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
# Токены в секции разделяются запятой; скобочные выражения (…~N) запятых не содержат.
_TOKEN_RE = re.compile(r"[^\s,]+(?:\s+[^\s,]+)*")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_path() -> Path:
    """Путь к файлу-сиду профиля относительно корня репозитория
    (или env-оверрайд ``ZAKUPKI_PROFILE_FILE``)."""
    override = os.environ.get("ZAKUPKI_PROFILE_FILE")
    if override:
        return Path(override)
    return _repo_root() / "docs" / "references" / "bbk-it-profile.md"


def _canonical_section(title: str) -> str | None:
    norm = title.strip().casefold()
    for key, aliases in _SECTION_ALIASES.items():
        if any(alias.casefold() == norm for alias in aliases):
            return key
    return None


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", value.replace(" ", ""))
    return float(match.group().replace(",", ".")) if match else None


def parse_keywords_text(text: str) -> dict[str, Any]:
    """Разбирает текст файла: имя, слова, компетенции и критерии поиска.

    Возвращает ``{"name", "keywords", "exclusion_words", "competencies",
    "okpd_codes", "nmck_min", "nmck_max"}``.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        heading = _HEADING_RE.match(stripped)
        if heading:
            current = _canonical_section(heading.group(1).strip())
            sections.setdefault(current or "", [])
            continue
        if current is None or not stripped:
            continue
        sections[current].append(line.rstrip())

    def tokens(key: str) -> list[str]:
        return _dedupe(
            t.strip().strip("\"'")
            for raw in sections.get(key, [])
            for t in _TOKEN_RE.findall(raw)
            if t
        )

    def first(key: str) -> str | None:
        values = sections.get(key)
        return values[0] if values else None

    return {
        "name": (first("name") or "").strip(),
        "keywords": tokens("keywords"),
        "exclusion_words": tokens("exclusion_words"),
        "competencies": "\n".join(sections.get("competencies", [])).strip(),
        "okpd_codes": tokens("okpd_codes"),
        "nmck_min": _parse_float(first("nmck_min")),
        "nmck_max": _parse_float(first("nmck_max")),
    }


def _resolve_competencies_file(comp: str, base_dirs: Iterable[Path]) -> str:
    """Если ``comp`` — однострочная ссылка на файл, подставляет его содержимое.

    JSON-блок (объект схемы Profile) ссылкой не считается. Перебирает
    ``base_dirs`` (обычно каталог исходного файла и корень репозитория)
    и возвращает либо содержимое найденного файла, либо исходную строку.
    """
    if not comp:
        return comp
    if "\n" in comp.strip():
        return comp
    # Компетенции — канонический JSON (BR-07): попытка разобрать как JSON-объект —
    # это блок, а не путь к файлу.
    try:
        parsed = json.loads(comp)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return comp
    candidate = Path(comp.strip())
    for base in base_dirs:
        ref = base / candidate
        try:
            if ref.is_file():
                return ref.read_text(encoding="utf-8")
        except OSError:
            continue
    return comp


def resolve_competencies_reference(
    seed: dict[str, Any], base_dirs: Iterable[Path] = ()
) -> dict[str, Any]:
    """Возвращает ``seed`` с подставленным содержимым ``competencies``-ссылки.

    Применяется там, где контент разобран без знания каталога исходного файла
    (например, web-импорт профиля): ссылки вида ``docs/references/…`` ищутся
    относительно корня репозитория (и переданных ``base_dirs``).
    """
    seed = dict(seed)
    seed["competencies"] = _resolve_competencies_file(
        seed.get("competencies", ""), [*base_dirs, _repo_root()]
    )
    return seed


def parse_keywords_file(path: Path | None = None) -> dict[str, Any]:
    """Читает и разбирает файл; компетенции-ссылку резолвит в содержимое файла."""
    target = path or _default_path()
    parsed = parse_keywords_text(target.read_text(encoding="utf-8"))
    return resolve_competencies_reference(parsed, (target.parent,))


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
