"""Поиск файла ТЗ в карточке закупки: маркеры имён, листинг и прямой поиск.

Константы и чистые хелперы имен/файлов вынесены из прежнего ``scoring_common/tz.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Маркеры ТЗ в имени файла (регистронезависимо). ``тз`` матчится отдельно
# границами слова (``aaa_тз_2.docx`` — совпадение, ``втзд`` — нет).
_TZ_PHRASES: tuple[str, ...] = (
    "техническое задание",
    "техническоезадание",
    "тех.задание",
    "тех задание",
)

# Маркер «Описание/описания/описанию/описании»: матчится по основе слова
# ``описан`` в начале лексемы, чтобы покрыть русские падежные формы имени файла.
_DESCRIPTION_RE = re.compile(r"(^|[^а-яa-z])описан[а-я]*($|[^а-яa-z])")

# Расширения архивированных файлов, внутри которых ищем ТЗ.
_ARCHIVE_EXTENSIONS: tuple[str, ...] = (
    ".7z",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".tbz2",
    ".xz",
    ".txz",
)

# Расширения документов, из которых умеем извлекать текст.
_PLAIN_TEXT_EXTENSIONS: tuple[str, ...] = (
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".csv",
    ".json",
    ".xml",
)

# Кодировки, в которых могут быть заданы имена записей архива. zipfile без
# UTF-8-флага декодирует имена как cp437 (латиница/«кракозябры»), поэтому русские
# имена (часто cp1251) нужно восстанавливать из исходных байт.
_MEMBER_NAME_ENCODINGS: tuple[str, ...] = ("utf-8", "cp1251", "cp866")

# Верхний предел длины имени записи архива: защита от патологически длинных имён
# (в таких же архивах Windows), способных вызвать ошибку "file name too long".
_MAX_MEMBER_NAME_LEN = 255

# Защитные ограничения.
_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 МБ на один файл
_MAX_ARCHIVE_ENTRIES = 500  # защита от zip-бомб


@dataclass(frozen=True)
class FileRef:
    """Ссылка на файл: имя + URL скачивания."""

    name: str
    url: str


def _normalize(name: str | None) -> str:
    return (name or "").strip().lower()


def _has_tz_marker(name: str | None) -> bool:
    """Явный маркер ТЗ: «техническое задание»/«тех.задание»/аббревиатура «тз».

    Площадки часто генерируют имена файлов с разделителями-подчёркиваниями
    (например, ``техническое_задание_по_модернизации.docx``), поэтому фразы
    сопоставляются по имени, где ``_`` и ``-`` приведены к пробелу. Аббревиатура
    ``тз`` ищется по границам слова на исходном имени.
    """
    low = _normalize(name)
    if re.search(r"(^|[^а-яa-z])(тз)($|[^а-яa-z])", low):
        return True
    spaced = re.sub(r"[_-]+", " ", low)
    return any(phrase in spaced for phrase in _TZ_PHRASES)


def is_description(name: str | None) -> bool:
    """Указывает ли имя на описание («описание/описания/описанию/описании»)."""
    return bool(_DESCRIPTION_RE.search(_normalize(name)))


def is_tz(name: str | None) -> bool:
    """Содержит ли имя файла маркер ТЗ или «описание» (без учёта регистра)."""
    return _has_tz_marker(name) or is_description(name)


def is_archive(name: str | None) -> bool:
    """Является ли файл архивом (по расширению)."""
    return _normalize(name).endswith(_ARCHIVE_EXTENSIONS)


def collect_files(record: dict[str, Any]) -> list[FileRef]:
    """Собрать все файлы карточки из ``files_json`` (включая ТЗ)."""
    refs: list[FileRef] = []
    for entry in record.get("files_json") or []:
        if isinstance(entry, dict) and entry.get("url"):
            refs.append(FileRef(str(entry.get("name") or ""), str(entry["url"])))
    return refs


def find_tz_file(record: dict[str, Any]) -> FileRef | None:
    """Прямой файл ТЗ (имя содержит маркер) либо None.

    Приоритет — явный маркер ТЗ («тех. задание»/«тз») над «описание»: если в
    карточке есть оба типа файлов, берётся настоящий ТЗ.
    """
    refs = collect_files(record)
    for ref in refs:
        if _has_tz_marker(ref.name):
            return ref
    for ref in refs:
        if is_description(ref.name):
            return ref
    return None


def find_description_file(record: dict[str, Any]) -> FileRef | None:
    """Прямой файл «описание» (имя содержит маркер описания) либо None.

    Используется как запасной источник текста: если в основном ТЗ нет
    требований к Исполнителю, берём текст документа «Описание».
    """
    for ref in collect_files(record):
        if is_description(ref.name):
            return ref
    return None
