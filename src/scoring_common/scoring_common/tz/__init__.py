"""Поиск и извлечение текста технического задания.

Общий код для стадий конвейера (scoring_service, analysis_service): поиск файла ТЗ
в карточке закупки и извлечение текста (в т.ч. из архивов). docx/pdf конвертируются
в Markdown через MarkItDown (Microsoft): сохраняются заголовки разделов и таблицы,
что используется RAG-чанкером анализа стоп-условий.

Стратегия поиска:
1. прямой файл — имя содержит маркер ТЗ (``техническое задание`` / ``тз``);
2. файл внутри архива — если прямого файла нет, перебираем архивы
   (zip/tar/7z и др.) и ищем запись с маркером ТЗ в имени.

Если файл не найден или текст не извлекается — возвращается ``None``.

Реализация разбита на подпакеты: ``files`` (маркеры имён и поиск), ``download``
(скачивание), ``archives`` (листинг/извлечение из архивов), ``extractors``
(docx/pdf → Markdown), ``text`` (очистка). Здесь — публичный вход ``extract_text``
и реэкспорт для совместимости с прежним модулем ``scoring_common/tz.py``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from scoring_common.tz.archives import (
    _decode_member_name,
    _extract_archive_member,
    _extract_from_7z,
    _extract_from_zip,
    find_description_in_archives,
    find_tz_in_archives,
)
from scoring_common.tz.download import _download
from scoring_common.tz.extractors import _decode, _extract_docx
from scoring_common.tz.files import (
    FileRef,
    _normalize,
    collect_files,
    find_description_file,
    find_tz_file,
    is_archive,
    is_tz,
)
from scoring_common.tz.text import clean_text

# TTL кэша извлечённого текста ТЗ: файлы закупки за час не меняются, повторно
# скачивать/конвертировать (в т.ч. из архивов) при каждом открытии карточки не нужно.
_TZ_TEXT_TTL_SECONDS = 3600.0

# Потолки кэша текста ТЗ — защита от неограниченного роста памяти на длинном
# процессе API:
# - максимум записей (LRU): при превышении вытесняются самые старые;
# - максимум символов на запись: тексты больше не кэшируются (отдаются, но не
#   удерживаются в памяти);
# - суммарный бюджет символов: при превышении вытесняются самые большие записи.
_TZ_TEXT_MAX_ENTRIES = 512
_TZ_TEXT_MAX_CHARS_PER_ENTRY = 2_000_000  # ~4 МБ UTF-8 на запись
_TZ_TEXT_MAX_TOTAL_CHARS = 20_000_000  # ~40 МБ суммарно

# Максимум записей в кэше найденных файлов ТЗ (FileRef крошечный — бюджет не нужен).
_TZ_REF_MAX_ENTRIES = 2048

# Кэш текста: ключ (url#inner, имя файла) -> (время вставки, текст | None).
# Кэшируется и неуспех (None), чтобы временная недоступность ЭТП не вызывала
# повторных скачиваний при каждом запросе. OrderedDict — порядок для LRU-эвикции.
_tz_text_cache: OrderedDict[tuple[str, str], tuple[float, str | None]] = OrderedDict()
# Кэш найденного файла ТЗ: ключ — сигнатура files_json (name, url) -> (время, FileRef|None).
# Покрывает листинг архивов: при тёплом кэше архивы повторно не скачиваются.
_tz_ref_cache: OrderedDict[tuple[tuple[str, str], ...], tuple[float, FileRef | None]] = (
    OrderedDict()
)
_tz_text_lock = threading.Lock()


def _record_signature(record: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Стабильная сигнатура карточки по её файлам (для ключа кэша find_tz_reference)."""
    return tuple(
        (str(entry.get("name") or ""), str(entry.get("url") or ""))
        for entry in (record.get("files_json") or [])
        if isinstance(entry, dict)
    )


def find_tz_reference(
    record: dict[str, Any], timeout: float = 30.0, verify_ssl: bool = True
) -> FileRef | None:
    """Стратегия поиска: прямой файл → внутри архивов."""
    direct = find_tz_file(record)
    if direct is not None:
        return direct
    return find_tz_in_archives(record, timeout=timeout, verify_ssl=verify_ssl)


def find_description_reference(
    record: dict[str, Any], timeout: float = 30.0, verify_ssl: bool = True
) -> FileRef | None:
    """Файл «описание»: прямой → внутри архивов (запасной источник текста)."""
    direct = find_description_file(record)
    if direct is not None:
        return direct
    return find_description_in_archives(record, timeout=timeout, verify_ssl=verify_ssl)


def extract_text(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str | None:
    """Извлечь Markdown-текст из файла ТЗ (в т.ч. из zip/7z-архива)."""
    url, sep, inner = ref.url.partition("#")
    name = _normalize(ref.name)
    if sep:
        # Запись внутри архива: формат определяем по содержимому (URL может быть
        # глухим, без расширения — например etp.gpb.ru ``/file/get/.../name/<hash>``).
        return _extract_archive_member(url, inner, timeout=timeout, verify_ssl=verify_ssl)
    if name.endswith(".7z"):
        return _extract_from_7z(ref, timeout=timeout, verify_ssl=verify_ssl)
    if name.endswith(".zip"):
        return _extract_from_zip(ref, timeout=timeout, verify_ssl=verify_ssl)
    if is_archive(name):
        return None  # прочие архивы (rar/tar) — требуют внешних утилит
    raw = _download(url, timeout=timeout, verify_ssl=verify_ssl)
    if raw is None:
        return None
    return _decode(raw, name)


def extract_text_cached(
    ref: FileRef,
    timeout: float = 30.0,
    ttl: float = _TZ_TEXT_TTL_SECONDS,
    verify_ssl: bool = True,
) -> str | None:
    """``extract_text`` с TTL-кэшем: повторно файл не скачивается/не извлекается.

    Ключ — ``(ref.url, ref.name)``: для записей внутри архива ``ref.url`` уже
    содержит ``#внутренний_путь``, так что разные записи одного zip не мешают
    друг другу. Кэшируется и ``None`` (файл не скачался/текст не извлёкся).
    Кэш ограничен: LRU по числу записей + суммарный бюджет символов; очень
    большие тексты (``_TZ_TEXT_MAX_CHARS_PER_ENTRY``) отдаются, но не кэшируются.
    """
    key = (ref.url, ref.name)
    now = time.monotonic()
    with _tz_text_lock:
        cached = _tz_text_cache.get(key)
        if cached is not None and now - cached[0] < ttl:
            # Актуальная запись: поднимаем в конец (LRU-порядок).
            _tz_text_cache.move_to_end(key)
            return cached[1]
    text = extract_text(ref, timeout=timeout, verify_ssl=verify_ssl)
    if text is not None and len(text) > _TZ_TEXT_MAX_CHARS_PER_ENTRY:
        return text  # слишком большой текст кэшировать не будем (безопасность памяти)
    with _tz_text_lock:
        _tz_text_cache[key] = (time.monotonic(), text)
        _tz_text_cache.move_to_end(key)
        _prune_tz_text_cache(time.monotonic(), ttl=ttl)
    return text


def find_tz_reference_cached(
    record: dict[str, Any],
    timeout: float = 30.0,
    ttl: float = _TZ_TEXT_TTL_SECONDS,
    verify_ssl: bool = True,
) -> FileRef | None:
    """``find_tz_reference`` с TTL-кэшем: листинг архивов повторно не скачивается.

    Ключ — сигнатура ``files_json`` (name, url) карточки. Кэшируется и ``None``.
    """
    key = _record_signature(record)
    now = time.monotonic()
    with _tz_text_lock:
        cached = _tz_ref_cache.get(key)
        if cached is not None and now - cached[0] < ttl:
            _tz_ref_cache.move_to_end(key)
            return cached[1]
    ref = find_tz_reference(record, timeout=timeout, verify_ssl=verify_ssl)
    with _tz_text_lock:
        _tz_ref_cache[key] = (time.monotonic(), ref)
        _tz_ref_cache.move_to_end(key)
        _prune_tz_ref_cache(time.monotonic(), ttl=ttl)
    return ref


def _prune_tz_text_cache(now: float, ttl: float = _TZ_TEXT_TTL_SECONDS) -> None:
    """Очистить кэш текста: просроченные записи, LRU-лимит и бюджет символов.

    Вызывается только под ``_tz_text_lock`` ПОСЛЕ вставки новой записи.
    ``ttl`` — эффективный порог жизни записи: должен совпадать с тем, что
    использует чтение (``extract_text_cached``).
    """
    expired = [k for k, (ts, _) in _tz_text_cache.items() if now - ts >= ttl]
    for key in expired:
        del _tz_text_cache[key]
    while len(_tz_text_cache) > _TZ_TEXT_MAX_ENTRIES:
        _tz_text_cache.popitem(last=False)
    # Бюджет по сумме символов: вытесняем самые большие записи.
    while _tz_text_cache:
        total = sum(len(text or "") for _, text in _tz_text_cache.values())
        if total <= _TZ_TEXT_MAX_TOTAL_CHARS:
            break
        largest_key = max(_tz_text_cache, key=lambda k: len(_tz_text_cache[k][1] or ""))
        del _tz_text_cache[largest_key]


def _prune_tz_ref_cache(now: float, ttl: float = _TZ_TEXT_TTL_SECONDS) -> None:
    """Очистить кэш найденных файлов ТЗ: просроченные и LRU-лимит записей."""
    expired = [k for k, (ts, _) in _tz_ref_cache.items() if now - ts >= ttl]
    for key in expired:
        del _tz_ref_cache[key]
    while len(_tz_ref_cache) > _TZ_REF_MAX_ENTRIES:
        _tz_ref_cache.popitem(last=False)


def clear_tz_text_cache() -> None:
    """Очистить кэши текста ТЗ и найденных файлов (для тестов)."""
    from scoring_common.tz.download import clear_download_cache

    clear_download_cache()
    with _tz_text_lock:
        _tz_text_cache.clear()
        _tz_ref_cache.clear()


__all__ = [
    "FileRef",
    "clean_text",
    "clear_tz_text_cache",
    "collect_files",
    "extract_text",
    "extract_text_cached",
    "find_description_reference",
    "find_tz_file",
    "find_tz_in_archives",
    "find_tz_reference",
    "find_tz_reference_cached",
    "is_archive",
    "is_tz",
    # Совместимость с прежним монолитным модулем scoring_common/tz.py.
    "_decode_member_name",
    "_extract_docx",
]
