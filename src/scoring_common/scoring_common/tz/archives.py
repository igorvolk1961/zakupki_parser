"""Поиск и извлечение ТЗ внутри архивов (zip/tar) и листинг записей."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import PurePosixPath
from typing import Any

from scoring_common.tz.download import _download
from scoring_common.tz.extractors import _decode
from scoring_common.tz.files import (
    _MAX_ARCHIVE_ENTRIES,
    _MAX_MEMBER_NAME_LEN,
    _MEMBER_NAME_ENCODINGS,
    FileRef,
    collect_files,
    is_archive,
    is_tz,
)


def _decode_member_name(name: str) -> str:
    """Восстановить читаемое имя записи архива из исходных байт.

    ``zipfile`` без UTF-8-флага декодирует имя как cp437, поэтому русские имена
    (cp1251) превращаются в «кракозябры». Возвращаем имя обратно в байты (cp437)
    и пробуем применить реальные кодировки (utf-8/cp1251/cp866).
    """
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for encoding in _MEMBER_NAME_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return name


def _archive_inner_names(url: str, timeout: float = 30.0) -> list[str]:
    """Имена записей внутри архива (листинг без распаковки целиком)."""
    raw = _download(url, timeout=timeout)
    if raw is None:
        return []
    names: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if len(info.filename) > _MAX_MEMBER_NAME_LEN:
                    continue
                names.append(_decode_member_name(info.filename))
                if len(names) >= _MAX_ARCHIVE_ENTRIES:
                    break
    except zipfile.BadZipFile:
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
                names = [m.name for m in tf.getmembers() if m.isfile()][:_MAX_ARCHIVE_ENTRIES]
        except tarfile.TarError:
            names = []
    return names


def find_tz_in_archives(record: dict[str, Any], timeout: float = 30.0) -> FileRef | None:
    """Файл ТЗ внутри архива: URL архива + путь записи внутри (через ``#``)."""
    for ref in collect_files(record):
        if not is_archive(ref.name):
            continue
        for inner in _archive_inner_names(ref.url, timeout=timeout):
            if is_tz(PurePosixPath(inner).name):
                return FileRef(inner, f"{ref.url}#{inner}")
    return None


def _extract_from_zip(ref: FileRef, timeout: float = 30.0) -> str | None:
    """Текст ТЗ внутри zip-архива (по имени записи в ``ref.url#inner``)."""
    raw = _download(ref.url, timeout=timeout)
    if raw is None:
        return None
    _, _, inner_path = ref.url.partition("#")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if len(info.filename) > _MAX_MEMBER_NAME_LEN:
                    continue
                member_name = _decode_member_name(info.filename)
                if inner_path and member_name != inner_path:
                    continue
                if inner_path or is_tz(PurePosixPath(member_name).name):
                    text = _decode(zf.read(info), member_name)
                    if text:
                        return text
    except zipfile.BadZipFile:
        pass
    return None
