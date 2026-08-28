"""Unit-тесты кэша извлечённого текста ТЗ (scoring_common.tz)."""

from __future__ import annotations

import time

import scoring_common.tz as tz
from scoring_common.tz import (
    clear_tz_text_cache,
    extract_text_cached,
    find_tz_reference_cached,
)
from scoring_common.tz.files import FileRef


def _calls(monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake_extract(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str | None:
        calls.append((ref.url, ref.name))
        return f"text:{ref.url}"

    monkeypatch.setattr("scoring_common.tz.extract_text", fake_extract)
    return calls


def test_extract_text_cached_extracts_once(monkeypatch) -> None:
    """Повторный запрос с тем же FileRef не переизвлекает текст."""
    clear_tz_text_cache()
    calls = _calls(monkeypatch)
    ref = FileRef("ТЗ.docx", "http://x/tz.docx")
    try:
        assert extract_text_cached(ref) == "text:http://x/tz.docx"
        assert extract_text_cached(ref) == "text:http://x/tz.docx"
        assert calls == [("http://x/tz.docx", "ТЗ.docx")]
    finally:
        clear_tz_text_cache()


def test_extract_text_cached_archive_members_are_distinct(monkeypatch) -> None:
    """Записи внутри архива кэшируются раздельно (ключ включает url#inner)."""
    clear_tz_text_cache()
    calls = _calls(monkeypatch)
    ref_zip = FileRef("ТЗ.docx", "http://x/a.zip#doc/ТЗ.docx")
    ref_other = FileRef("приложение.docx", "http://x/a.zip#doc/приложение.docx")
    try:
        assert extract_text_cached(ref_zip) == "text:http://x/a.zip#doc/ТЗ.docx"
        assert extract_text_cached(ref_other) == "text:http://x/a.zip#doc/приложение.docx"
        assert extract_text_cached(ref_zip) == "text:http://x/a.zip#doc/ТЗ.docx"
        assert calls == [
            ("http://x/a.zip#doc/ТЗ.docx", "ТЗ.docx"),
            ("http://x/a.zip#doc/приложение.docx", "приложение.docx"),
        ]
    finally:
        clear_tz_text_cache()


def test_extract_text_cached_caches_none(monkeypatch) -> None:
    """Неуспех (None) тоже кэшируется: повторно файл не скачивается."""
    clear_tz_text_cache()
    calls: list[tuple[str, str]] = []

    def failing_extract(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str | None:
        calls.append((ref.url, ref.name))
        return None

    monkeypatch.setattr("scoring_common.tz.extract_text", failing_extract)
    ref = FileRef("ТЗ.pdf", "http://x/tz.pdf")
    try:
        assert extract_text_cached(ref) is None
        assert extract_text_cached(ref) is None
        assert calls == [("http://x/tz.pdf", "ТЗ.pdf")]
    finally:
        clear_tz_text_cache()


def test_extract_text_cached_ttl_expiry(monkeypatch) -> None:
    """По истечении TTL текст извлекается заново."""
    clear_tz_text_cache()
    calls = _calls(monkeypatch)
    ref = FileRef("ТЗ.docx", "http://x/tz.docx")
    try:
        assert extract_text_cached(ref, ttl=60.0) == "text:http://x/tz.docx"
        # Форсируем истечение TTL: время вставки в прошлом.
        _, text = tz._tz_text_cache[(ref.url, ref.name)]
        tz._tz_text_cache[(ref.url, ref.name)] = (time.monotonic() - 3600.0, text)
        assert extract_text_cached(ref, ttl=60.0) == "text:http://x/tz.docx"
        assert len(calls) == 2
    finally:
        clear_tz_text_cache()


def test_extract_text_cached_prunes_expired(monkeypatch) -> None:
    """Просроченные записи удаляются при вставке, а не накапливаются."""
    clear_tz_text_cache()
    _calls(monkeypatch)
    old = FileRef("старое.docx", "http://x/old.docx")
    new = FileRef("новое.docx", "http://x/new.docx")
    try:
        assert extract_text_cached(old) == "text:http://x/old.docx"
        # Искусственно состариваем запись.
        _, text = tz._tz_text_cache[("http://x/old.docx", "старое.docx")]
        tz._tz_text_cache[("http://x/old.docx", "старое.docx")] = (
            time.monotonic() - 7200.0,
            text,
        )
        assert extract_text_cached(new) == "text:http://x/new.docx"
        assert ("http://x/old.docx", "старое.docx") not in tz._tz_text_cache
        assert ("http://x/new.docx", "новое.docx") in tz._tz_text_cache
    finally:
        clear_tz_text_cache()


def test_extract_text_cached_lru_bound(monkeypatch) -> None:
    """Кэш ограничен по числу записей: старейшая вытесняется (LRU)."""
    old_max = tz._TZ_TEXT_MAX_ENTRIES
    tz._TZ_TEXT_MAX_ENTRIES = 3
    clear_tz_text_cache()
    calls = _calls(monkeypatch)
    try:
        refs = [FileRef(f"файл{i}.docx", f"http://x/f{i}.docx") for i in range(5)]
        for ref in refs:
            assert extract_text_cached(ref) == f"text:http://x/f{refs.index(ref)}.docx"
        # Осталось максимум 3 записи (первая вытеснена).
        assert len(tz._tz_text_cache) <= 3
        # Вытесненная запись извлекается заново (кэш не вернул её молча).
        assert extract_text_cached(refs[0]) == "text:http://x/f0.docx"
        assert len(calls) == 6
    finally:
        clear_tz_text_cache()
        tz._TZ_TEXT_MAX_ENTRIES = old_max


def test_find_tz_reference_cached_extracts_once(monkeypatch) -> None:
    """Повторный поиск файла ТЗ с той же карточкой не повторяется (кэш)."""
    clear_tz_text_cache()
    find_calls: list[int] = []
    record = {
        "files_json": [
            {"name": "приложение.zip", "url": "http://x/a.zip"},
            {"name": "смета.xlsx", "url": "http://x/smeta.xlsx"},
        ]
    }

    def fake_find(rec: dict, timeout: float = 30.0, verify_ssl: bool = True) -> FileRef | None:
        find_calls.append(1)
        return FileRef("ТЗ.docx", "http://x/a.zip#doc/ТЗ.docx")

    monkeypatch.setattr("scoring_common.tz.find_tz_reference", fake_find)
    try:
        assert find_tz_reference_cached(record) == FileRef("ТЗ.docx", "http://x/a.zip#doc/ТЗ.docx")
        assert find_tz_reference_cached(record) == FileRef("ТЗ.docx", "http://x/a.zip#doc/ТЗ.docx")
        assert find_calls == [1]
    finally:
        clear_tz_text_cache()


def test_find_tz_reference_cached_distinct_cards(monkeypatch) -> None:
    """Карточки с разным набором файлов кэшируются раздельно."""
    clear_tz_text_cache()
    find_calls: list[int] = []

    def fake_find(rec: dict, timeout: float = 30.0, verify_ssl: bool = True) -> FileRef | None:
        find_calls.append(1)
        return FileRef("ТЗ.docx", "http://x/a.zip#doc/ТЗ.docx")

    monkeypatch.setattr("scoring_common.tz.find_tz_reference", fake_find)
    card_a = {"files_json": [{"name": "a.zip", "url": "http://x/a.zip"}]}
    card_b = {"files_json": [{"name": "b.zip", "url": "http://x/b.zip"}]}
    try:
        assert find_tz_reference_cached(card_a) is not None
        assert find_tz_reference_cached(card_b) is not None
        assert find_tz_reference_cached(card_a) is not None
        assert len(find_calls) == 2  # a кэширован, b — отдельный ключ
    finally:
        clear_tz_text_cache()


def test_extract_text_cached_per_entry_cap(monkeypatch) -> None:
    """Очень большой текст отдаётся, но не кэшируется (бюджет памяти)."""
    old_cap = tz._TZ_TEXT_MAX_CHARS_PER_ENTRY
    tz._TZ_TEXT_MAX_CHARS_PER_ENTRY = 10
    clear_tz_text_cache()
    calls: list[tuple[str, str]] = []

    def big_extract(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str | None:
        calls.append((ref.url, ref.name))
        return "X" * 100

    monkeypatch.setattr("scoring_common.tz.extract_text", big_extract)
    ref = FileRef("ТЗ.docx", "http://x/tz.docx")
    try:
        assert extract_text_cached(ref) == "X" * 100
        assert len(tz._tz_text_cache) == 0  # в кэш не попал
        assert extract_text_cached(ref) == "X" * 100
        assert len(calls) == 2  # повторно извлечён
    finally:
        clear_tz_text_cache()
        tz._TZ_TEXT_MAX_CHARS_PER_ENTRY = old_cap


def test_extract_text_cached_total_budget(monkeypatch) -> None:
    """Суммарный бюджет символов соблюдается: вытесняется самая большая запись."""
    old_budget = tz._TZ_TEXT_MAX_TOTAL_CHARS
    old_max = tz._TZ_TEXT_MAX_ENTRIES
    tz._TZ_TEXT_MAX_TOTAL_CHARS = 100
    tz._TZ_TEXT_MAX_ENTRIES = 10**6  # бюджет по байтам должен сработать раньше LRU
    clear_tz_text_cache()
    sizes = {"s": 40, "b": 70}
    monkeypatch.setattr(
        "scoring_common.tz.extract_text",
        lambda ref, timeout=30.0, verify_ssl=True: ref.name * sizes[ref.name],
    )
    small = FileRef("s", "http://x/small.docx")
    big = FileRef("b", "http://x/b.docx")
    try:
        assert extract_text_cached(small) == "s" * 40
        assert extract_text_cached(big) == "b" * 70
        # После вставки большой записи сумма (40+70=110) превысила бюджет 100:
        # вытеснена самая большая (big), small остался.
        assert ("http://x/small.docx", "s") in tz._tz_text_cache
        assert ("http://x/big.docx", "b") not in tz._tz_text_cache
    finally:
        clear_tz_text_cache()
        tz._TZ_TEXT_MAX_TOTAL_CHARS = old_budget
        tz._TZ_TEXT_MAX_ENTRIES = old_max


def test_prune_uses_effective_ttl(monkeypatch) -> None:
    """Prune и чтение используют один порог TTL: ttl > дефолта не теряет записи."""
    clear_tz_text_cache()
    calls = _calls(monkeypatch)
    ref = FileRef("ТЗ.docx", "http://x/tz.docx")
    other = FileRef("другое.docx", "http://x/other.docx")
    try:
        assert extract_text_cached(ref, ttl=7200.0) == "text:http://x/tz.docx"
        # Запись «старше» дефолтного TTL (3600), но моложе эффективного (7200).
        _, text = tz._tz_text_cache[(ref.url, ref.name)]
        tz._tz_text_cache[(ref.url, ref.name)] = (time.monotonic() - 4000.0, text)
        # Вставка другой записи запускает prune: с эффективным ttl (7200) старая
        # запись (4000 с) сохраняется; с дефолтным (3600) была бы удалена.
        assert extract_text_cached(other, ttl=7200.0) == "text:http://x/other.docx"
        assert (ref.url, ref.name) in tz._tz_text_cache
        # Сама состаренная запись всё ещё попадает в кэш при чтении.
        assert extract_text_cached(ref, ttl=7200.0) == "text:http://x/tz.docx"
        assert len(calls) == 2
    finally:
        clear_tz_text_cache()


def test_download_cached(monkeypatch) -> None:
    """Повторное скачивание того же URL отдаётся из кэша (без httpx)."""
    from scoring_common.tz.download import _download, clear_download_cache

    clear_download_cache()
    calls: list[str] = []

    class _FakeResp:
        content: bytes

        def __init__(self, content: bytes) -> None:
            self.content = content
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _FakeResp:
            calls.append(url)
            return _FakeResp(b"archive-bytes")

    monkeypatch.setattr("scoring_common.tz.download.httpx.Client", _FakeClient)
    try:
        assert _download("http://x/a.zip") == b"archive-bytes"
        assert _download("http://x/a.zip") == b"archive-bytes"
        assert calls == ["http://x/a.zip"]  # второй вызов — из кэша
    finally:
        clear_download_cache()


def test_download_cache_shares_archive_bytes(monkeypatch) -> None:
    """Листинг и извлечение одного архива делят одно скачивание (url#inner)."""
    from scoring_common.tz.download import _download, clear_download_cache

    clear_download_cache()
    calls: list[str] = []

    class _FakeResp:
        content: bytes

        def __init__(self, content: bytes) -> None:
            self.content = content
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _FakeResp:
            calls.append(url)
            return _FakeResp(b"data:" + url.encode())

    monkeypatch.setattr("scoring_common.tz.download.httpx.Client", _FakeClient)
    try:
        # Листинг архива.
        assert _download("http://x/a.zip") == b"data:http://x/a.zip"
        # Извлечение члена того же архива: url#inner → тот же кэш-ключ.
        assert _download("http://x/a.zip#doc/ТЗ.docx") == b"data:http://x/a.zip"
        assert calls == ["http://x/a.zip"]  # одно скачивание на архив
    finally:
        clear_download_cache()


def test_download_caches_none(monkeypatch) -> None:
    """Неуспех скачивания (None) кэшируется: повторно httpx не вызывается."""
    import httpx

    from scoring_common.tz.download import _download, clear_download_cache

    clear_download_cache()
    calls: list[str] = []

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> None:
            calls.append(url)
            raise httpx.ConnectError("network down")

    monkeypatch.setattr("scoring_common.tz.download.httpx.Client", _FakeClient)
    try:
        assert _download("http://x/a.zip") is None
        assert _download("http://x/a.zip") is None
        assert calls == ["http://x/a.zip"]
    finally:
        clear_download_cache()
