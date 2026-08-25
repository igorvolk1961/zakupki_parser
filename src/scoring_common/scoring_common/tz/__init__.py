"""Поиск и извлечение текста технического задания.

Общий код для стадий конвейера (scoring_service, analysis_service): поиск файла ТЗ
в карточке закупки и извлечение текста (в т.ч. из архивов). docx/pdf конвертируются
в Markdown через MarkItDown (Microsoft): сохраняются заголовки разделов и таблицы,
что используется RAG-чанкером анализа стоп-условий.

Стратегия поиска:
1. прямой файл — имя содержит маркер ТЗ (``техническое задание`` / ``тз``);
2. файл внутри архива — если прямого файла нет, перебираем архивы
   (zip/tar и др.) и ищем запись с маркером ТЗ в имени.

Если файл не найден или текст не извлекается — возвращается ``None``.

Реализация разбита на подпакеты: ``files`` (маркеры имён и поиск), ``download``
(скачивание), ``archives`` (листинг/извлечение из архивов), ``extractors``
(docx/pdf → Markdown), ``text`` (очистка). Здесь — публичный вход ``extract_text``
и реэкспорт для совместимости с прежним модулем ``scoring_common/tz.py``.
"""

from __future__ import annotations

from typing import Any

from scoring_common.tz.archives import _extract_from_zip, find_tz_in_archives
from scoring_common.tz.download import _download
from scoring_common.tz.extractors import _decode
from scoring_common.tz.files import (
    FileRef,
    _normalize,
    collect_files,
    find_tz_file,
    is_archive,
    is_tz,
)
from scoring_common.tz.text import clean_text


def find_tz_reference(record: dict[str, Any], timeout: float = 30.0) -> FileRef | None:
    """Стратегия поиска: прямой файл → внутри архивов."""
    direct = find_tz_file(record)
    if direct is not None:
        return direct
    return find_tz_in_archives(record, timeout=timeout)


def extract_text(ref: FileRef, timeout: float = 30.0) -> str | None:
    """Извлечь Markdown-текст из файла ТЗ (в т.ч. из zip-архива)."""
    url, _, _ = ref.url.partition("#")
    name = _normalize(ref.name)
    if ".zip#" in ref.url:
        return _extract_from_zip(ref, timeout=timeout)
    if is_archive(name):
        return None  # прочие архивы (rar/7z/tar) — требуют внешних утилит
    raw = _download(url, timeout=timeout)
    if raw is None:
        return None
    return _decode(raw, name)


__all__ = [
    "FileRef",
    "clean_text",
    "collect_files",
    "extract_text",
    "find_tz_file",
    "find_tz_in_archives",
    "find_tz_reference",
    "is_archive",
    "is_tz",
]
