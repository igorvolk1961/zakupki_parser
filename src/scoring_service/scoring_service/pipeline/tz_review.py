"""Поиск и извлечение текста ТЗ — общий код в ``scoring_common.tz``.

Шим для обратной совместимости импортов scoring_service.
"""

from __future__ import annotations

from scoring_common.tz import (  # noqa: F401
    FileRef,
    clean_text,
    collect_files,
    extract_text,
    find_tz_file,
    find_tz_in_archives,
    find_tz_reference,
    is_archive,
    is_tz,
)

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
