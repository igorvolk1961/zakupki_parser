"""Разбор файла ключевых слов ``data/key_words.md`` (сид для профилей, R8).

Формат (см. ``data/key_words.md``): секции, начинающиеся с ``**Заголовок**``.
Разбираются две секции:
- ``Ключевые слова`` — позитивные выражения (попадают в ``Profile.keywords``);
- ``Минус слова`` — слова-исключения (``Profile.exclusion_words``).

Каждая секция — список выражений через запятую. Допустимые формы:
- ``слов*`` — слово с усечением;
- ``(фраза* фраза*)~N`` — близость слов (порядок в пределах N слов);
- ``точная фраза`` / ``"точная фраза"`` — фраза как есть.

Парсер нормализует выражения (снимает кавычки, обрезает пробелы) и сохраняет
исходный синтаксис ``*``/``~N`` — интерпретация происходит в фильтрации (Этап 3).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

SECTION_KEYWORDS = "Ключевые слова"
SECTION_EXCLUSIONS = "Минус слова"

_HEADING_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
# Токены в секции разделяются запятой; скобочные выражения (…~N) запятых не содержат.
_TOKEN_RE = re.compile(r"[^\s,]+(?:\s+[^\s,]+)*")


def _default_path() -> Path:
    """Путь к ``data/key_words.md`` относительно корня репозитория (или env-оверрайд)."""
    override = os.environ.get("ZAKUPKI_KEYWORDS_FILE")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "data" / "key_words.md"


def parse_keywords_text(text: str) -> dict[str, list[str]]:
    """Разбирает текст файла на позитивные слова и слова-исключения.

    Возвращает ``{"keywords": [...], "exclusion_words": [...]}``.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        heading = _HEADING_RE.match(stripped)
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, [])
            continue
        if current is None or not stripped:
            continue
        # Убираем кавычки у токенов; пустые отбрасываем.
        tokens = [t.strip().strip("\"'") for t in _TOKEN_RE.findall(stripped)]
        sections[current].extend(t for t in tokens if t)

    keywords = _dedupe(sections.get(SECTION_KEYWORDS, []))
    exclusion_words = _dedupe(sections.get(SECTION_EXCLUSIONS, []))
    return {"keywords": keywords, "exclusion_words": exclusion_words}


def parse_keywords_file(path: Path | None = None) -> dict[str, list[str]]:
    """Читает и разбирает файл ключевых слов."""
    target = path or _default_path()
    return parse_keywords_text(target.read_text(encoding="utf-8"))


def default_keywords_seed(path: Path | None = None) -> dict[str, Any]:
    """Сид профиля по умолчанию из ``data/key_words.md`` (R8)."""
    parsed = parse_keywords_file(path)
    return {
        "name": "default",
        "enabled": True,
        "is_active": True,
        "competencies": "",
        "keywords": parsed["keywords"],
        "exclusion_words": parsed["exclusion_words"],
        "keyword_context_regexes": {},
        "questions": [],
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
