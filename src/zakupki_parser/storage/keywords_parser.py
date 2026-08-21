"""Разбор файла ключевых слов/компетенций профиля (сид, R8).

Формат — markdown с секциями ``**Заголовок**``. Поддерживаются имена секций в
двух вариантах (русском и в стиле агрегаторов ТендерПлан/ТендерЛэнд):
- ``name`` — имя профиля (например ``bbk-it``);
- ``Ключевые слова`` / ``keywords`` — позитивные выражения (type=keyword);
- ``Минус слова`` / ``exclussion_words`` / ``exclusion_words`` — слова-исключения
  (type=exclusion);
- ``Компетенции`` / ``competencies`` — текст компетенций (блок) ЛИБО путь к файлу
  с текстом компетенций (например ``docs/references/bbk-it-site.md``) — в этом
  случае содержимое файла подставляется при разборе ``parse_keywords_file``.

Каждая секция слов — список выражений через запятую. Допустимые формы:
- ``слов*`` — слово с усечением;
- ``(фраза* фраза*)~N`` — не более N слов между токенами (проксимити);
- ``точная фраза`` / ``"точная фраза"`` — фраза как есть.

Парсер нормализует выражения (снимает кавычки, обрезает пробелы) и сохраняет
исходный синтаксис ``*``/``~N`` — интерпретация происходит в фильтрации (Этап 3).
Слова попадают в таблицу ``keywords`` (канонический источник, ER: PROFILE -> KEYWORD).
"""

from __future__ import annotations

import os
import re
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
}

_HEADING_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
# Токены в секции разделяются запятой; скобочные выражения (…~N) запятых не содержат.
_TOKEN_RE = re.compile(r"[^\s,]+(?:\s+[^\s,]+)*")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_path() -> Path:
    """Путь к ``data/profile.md`` относительно корня репозитория (или env-оверрайд)."""
    override = os.environ.get("ZAKUPKI_PROFILE_FILE")
    if override:
        return Path(override)
    return _repo_root() / "data" / "profile.md"


def _canonical_section(title: str) -> str | None:
    norm = title.strip().casefold()
    for key, aliases in _SECTION_ALIASES.items():
        if any(alias.casefold() == norm for alias in aliases):
            return key
    return None


def parse_keywords_text(text: str) -> dict[str, Any]:
    """Разбирает текст файла на имя, слова и компетенции.

    Возвращает ``{"name": str, "keywords": [...], "exclusion_words": [...],
    "competencies": str}``.
    """
    sections: dict[str, Any] = {}
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
        if current in ("name", "competencies"):
            # Имя — первая строка; компетенции — блок строк.
            sections[current].append(line.rstrip())
            continue
        # Убираем кавычки у токенов; пустые отбрасываем.
        tokens = [t.strip().strip("\"'") for t in _TOKEN_RE.findall(stripped)]
        sections[current].extend(t for t in tokens if t)

    name = (sections.get("name") or [""])[0].strip()
    keywords = _dedupe(sections.get("keywords", []))
    exclusion_words = _dedupe(sections.get("exclusion_words", []))
    competencies = "\n".join(sections.get("competencies", [])).strip()
    return {
        "name": name,
        "keywords": keywords,
        "exclusion_words": exclusion_words,
        "competencies": competencies,
    }


def parse_keywords_file(path: Path | None = None) -> dict[str, Any]:
    """Читает и разбирает файл; компетенции-ссылку резолвит в содержимое файла."""
    target = path or _default_path()
    parsed = parse_keywords_text(target.read_text(encoding="utf-8"))
    comp = parsed.get("competencies", "")
    # Если компетенции — однострочная ссылка на файл, подставляем его содержимое
    # (относительно каталога исходного файла или корня репозитория).
    if comp and "\n" not in comp.strip():
        candidate = Path(comp.strip())
        for base in (target.parent, _repo_root()):
            ref = base / candidate
            if ref.is_file():
                parsed["competencies"] = ref.read_text(encoding="utf-8")
                break
    return parsed


def default_keywords_seed(path: Path | None = None) -> dict[str, Any]:
    """Сид профиля из файла (R8): имя, слова и компетенции."""
    parsed = parse_keywords_file(path)
    return {
        "name": parsed.get("name") or "default",
        "enabled": True,
        "is_active": True,
        "competencies": parsed.get("competencies", ""),
        "keywords": parsed.get("keywords", []),
        "exclusion_words": parsed.get("exclusion_words", []),
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
