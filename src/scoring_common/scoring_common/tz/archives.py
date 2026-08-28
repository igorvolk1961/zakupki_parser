"""Поиск и извлечение ТЗ внутри архивов (zip/tar/7z) и листинг записей."""

from __future__ import annotations

import io
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
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


def _list_zip(raw: bytes) -> list[str] | None:
    """Имена записей zip-архива (None, если это не zip)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names: list[str] = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if len(info.filename) > _MAX_MEMBER_NAME_LEN:
                    continue
                names.append(_decode_member_name(info.filename))
                if len(names) >= _MAX_ARCHIVE_ENTRIES:
                    break
            return names
    except zipfile.BadZipFile:
        return None


def _list_tar(raw: bytes) -> list[str] | None:
    """Имена записей tar/tar.gz/tar.bz2 (None, если это не tar)."""
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
            return [m.name for m in tf.getmembers() if m.isfile()][:_MAX_ARCHIVE_ENTRIES]
    except tarfile.TarError:
        return None


def _list_7z(raw: bytes) -> list[str] | None:
    """Имена записей 7z-архива (None, если py7zr недоступен или это не 7z).

    ``getnames`` возвращает читаемые Unicode-имена (в отличие от ``zipfile``,
    который без UTF-8-флага даёт cp437-«кракозябры»), поэтому декодирование имён
    здесь не требуется.
    """
    try:
        import py7zr  # lazy: 7z — опциональная зависимость
    except ImportError:
        return None
    try:
        with py7zr.SevenZipFile(io.BytesIO(raw), mode="r") as archive:
            names = [
                n
                for n in archive.getnames()
                if not n.endswith("/") and len(n) <= _MAX_MEMBER_NAME_LEN
            ]
            return names[:_MAX_ARCHIVE_ENTRIES]
    except Exception:  # noqa: BLE001 - битый архив/неизвестный формат
        return None


def _archive_inner_names(url: str, timeout: float = 30.0) -> list[str]:
    """Имена записей внутри архива (листинг без распаковки целиком).

    Пробуем форматы по очереди (zip → tar → 7z): первый распознанный отдаёт
    список имён (пустой список — не ошибка, а пустой архив/нет записей).
    """
    raw = _download(url, timeout=timeout)
    if raw is None:
        return []
    names = _list_zip(raw)
    if names is None:
        names = _list_tar(raw)
    if names is None:
        names = _list_7z(raw)
    return names or []


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


def _extract_7z_member(raw: bytes, member_name: str) -> bytes | None:
    """Байты указанной записи 7z-архива (распаковка в память через временный файл).

    py7zr 1.x не читает записи напрямую в память (``read``), поэтому выбранный
    член распаковывается во временную директорию и читается обратно. Временная
    директория удаляется сразу после чтения.
    """
    try:
        import py7zr  # lazy: 7z — опциональная зависимость
    except ImportError:
        return None
    try:
        with py7zr.SevenZipFile(io.BytesIO(raw), mode="r") as archive:
            if member_name not in archive.getnames():
                return None
            with tempfile.TemporaryDirectory() as tmpdir:
                archive.extract(path=tmpdir, targets=[member_name])
                tmp_path = Path(tmpdir).resolve()
                member_path = (tmp_path / member_name).resolve()
                # Не даём записи с «../» выйти за пределы временной директории.
                if not member_path.is_relative_to(tmp_path) or not member_path.is_file():
                    return None
                return member_path.read_bytes()
    except Exception:  # noqa: BLE001 - битый архив/неизвестный формат
        return None


def _extract_from_7z(ref: FileRef, timeout: float = 30.0) -> str | None:
    """Текст ТЗ внутри 7z-архива (по имени записи в ``ref.url#inner``)."""
    raw = _download(ref.url, timeout=timeout)
    if raw is None:
        return None
    _, _, inner_path = ref.url.partition("#")
    if inner_path:
        data = _extract_7z_member(raw, inner_path)
        return _decode(data, inner_path) if data else None
    # Без внутреннего пути: находим запись с маркером ТЗ по имени.
    for member in _list_7z(raw) or []:
        if is_tz(PurePosixPath(member).name):
            data = _extract_7z_member(raw, member)
            if data:
                text = _decode(data, member)
                if text:
                    return text
    return None
