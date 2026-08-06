"""Классификация метаданных файлов закупки (без скачивания).

Парсер НЕ скачивает файлы: он сохраняет метаданные (имя/URL скачивания с ЭТП)
из карточки закупки. Среди них выделяется техническое задание (по имени файла),
которое кладётся в отдельные поля ``technical_spec_name``/``technical_spec_url``,
остальные — в ``files_json``.
"""

from __future__ import annotations

# Ключевые слова для определения ТЗ по имени файла (не конфигурируется).
TECHNICAL_SPEC_KEYWORDS: tuple[str, ...] = ("техническое задание",)


def _matches_keywords(filename: str | None) -> bool:
    """Содержит ли имя файла хотя бы одно ключевое слово ТЗ (без учёта регистра)."""
    if not filename:
        return False
    low = filename.lower()
    return any(k.lower() in low for k in TECHNICAL_SPEC_KEYWORDS)


def split_technical_spec(
    files: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Разделяет файлы на техническое задание и остальные (по имени)."""
    ts = [f for f in files if _matches_keywords(f.get("name"))]
    others = [f for f in files if f not in ts]
    return ts, others
