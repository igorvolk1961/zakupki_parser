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
"""

from __future__ import annotations

import io
import re
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import httpx

# Маркеры ТЗ в имени файла (регистронезависимо). ``тз`` матчится отдельно
# границами слова (``aaa_тз_2.docx`` — совпадение, ``втзд`` — нет).
_TZ_PHRASES: tuple[str, ...] = (
    "техническое задание",
    "техническоезадание",
    "тех.задание",
    "тех задание",
)

# Расширения архивированных файлов, внутри которых ищем ТЗ.
_ARCHIVE_EXTENSIONS: tuple[str, ...] = (
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


def is_tz(name: str | None) -> bool:
    """Содержит ли имя файла маркер ТЗ (без учёта регистра)."""
    low = _normalize(name)
    if re.search(r"(^|[^а-яa-z])(тз)($|[^а-яa-z])", low):
        return True
    return any(phrase in low for phrase in _TZ_PHRASES)


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
    """Прямой файл ТЗ (имя содержит маркер) либо None."""
    for ref in collect_files(record):
        if is_tz(ref.name):
            return ref
    return None


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


def find_tz_reference(record: dict[str, Any], timeout: float = 30.0) -> FileRef | None:
    """Стратегия поиска: прямой файл → внутри архивов."""
    direct = find_tz_file(record)
    if direct is not None:
        return direct
    return find_tz_in_archives(record, timeout=timeout)


def _download(url: str, timeout: float = 30.0, max_bytes: int = _MAX_FILE_BYTES) -> bytes | None:
    """Скачать файл (с защитой от превышения размера)."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url.split("#", 1)[0])
            resp.raise_for_status()
            if int(resp.headers.get("content-length", "0") or 0) > max_bytes:
                return None
            return resp.content[:max_bytes]
    except httpx.HTTPError:
        return None


def _decode(raw: bytes, name: str) -> str | None:
    """Извлечь текст из байт по расширению (docx/pdf — Markdown через MarkItDown)."""
    ext = _normalize(name)
    for candidate in _PLAIN_TEXT_EXTENSIONS:
        if ext.endswith(candidate):
            for encoding in ("utf-8", "cp1251"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return None
    if ext.endswith(".docx"):
        return _extract_docx(raw)
    if ext.endswith(".pdf"):
        return _extract_pdf(raw)
    return None


# Ленивый MarkItDown (Microsoft): конвертация docx/pdf в Markdown с сохранением
# структуры — заголовки разделов (Heading 1/2/3, layout PDF) и таблицы. Это даёт
# RAG-чанкеру надёжные границы разделов и не теряет табличные требования ТЗ.
_markitdown: Any | bool | None = None


def _markitdown_instance() -> Any | None:
    global _markitdown
    if _markitdown is None:
        try:
            from markitdown import MarkItDown

            _markitdown = MarkItDown()
        except Exception:  # noqa: BLE001 - библиотека недоступна (best-effort)
            _markitdown = False
    return _markitdown if _markitdown is not False else None


def _convert_markdown(raw: bytes, extension: str) -> str | None:
    """Конвертировать документ в Markdown (заголовки/таблицы сохраняются)."""
    md = _markitdown_instance()
    if md is None:
        return None
    try:
        result = md.convert_stream(io.BytesIO(raw), file_extension=extension)
        text = (result.text_content or "").strip()
        return text or None
    except Exception:  # noqa: BLE001 - битый файл/неизвестный формат
        return None


def _extract_docx(raw: bytes) -> str | None:
    """Markdown из DOCX (mammoth: заголовки по стилям, таблицы сохраняются)."""
    return _convert_markdown(raw, ".docx")


def _extract_pdf(raw: bytes) -> str | None:
    """Markdown из PDF (pdfplumber: таблицы по layout, fallback pdfminer)."""
    return _convert_markdown(raw, ".pdf")


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


def clean_text(text: str) -> str:
    """Очистить извлечённый текст от мусора."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Управляющие символы (кроме переноса строки и табуляции).
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Схлопывание пробелов/табов и пустых строк.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Отбрасывание "мусорных" длинных строк без пробелов (base64 и т.п.).
    text = "\n".join(
        line for line in text.splitlines() if not (len(line) > 300 and " " not in line)
    )
    return text.strip()
