"""Unit-тесты поиска и извлечения ТЗ из 7z-архивов (scoring_common.tz)."""

from __future__ import annotations

import io

from scoring_common.tz import extract_text, find_tz_reference
from scoring_common.tz.archives import _list_7z
from scoring_common.tz.files import FileRef, is_archive


def _make_7z(files: dict[str, bytes]) -> bytes:
    """Собрать 7z-архив в память (имена записей — как в архивах ЭТП)."""
    from py7zr import SevenZipFile

    buf = io.BytesIO()
    with SevenZipFile(buf, "w") as archive:
        for name, content in files.items():
            archive.writestr(content, name)
    return buf.getvalue()


def _patch_download(monkeypatch, blob: bytes) -> None:
    """Отдаём байты архива из ``_download`` (без обращения в сеть)."""
    from scoring_common.tz import archives

    monkeypatch.setattr(archives, "_download", lambda url, timeout=30.0: blob)


def test_is_archive_recognizes_7z() -> None:
    assert is_archive("Закупочная_документация.7z")
    assert is_archive("doc.zip")
    assert not is_archive("technic.md")


def test_list_7z_names(monkeypatch) -> None:
    blob = _make_7z(
        {
            "doc/Техническое задание.txt": "текст ТЗ".encode(),
            "doc/приложение.pdf": b"pdf",
        }
    )
    _patch_download(monkeypatch, blob)
    assert _list_7z(blob) == ["doc/Техническое задание.txt", "doc/приложение.pdf"]


def test_find_tz_reference_in_7z(monkeypatch) -> None:
    blob = _make_7z(
        {
            "doc/Техническое задание.txt": "текст ТЗ".encode(),
            "doc/приложение.pdf": b"pdf",
        }
    )
    _patch_download(monkeypatch, blob)
    record = {
        "files_json": [
            {"name": "Закупочная_документация.7z", "url": "http://x/doc.7z"},
        ]
    }
    ref = find_tz_reference(record)
    assert ref is not None
    assert ref.name == "doc/Техническое задание.txt"
    assert ref.url == "http://x/doc.7z#doc/Техническое задание.txt"


def test_extract_text_from_7z(monkeypatch) -> None:
    blob = _make_7z(
        {
            "doc/Техническое задание.txt": "текст ТЗ".encode(),
            "doc/приложение.pdf": b"pdf",
        }
    )
    _patch_download(monkeypatch, blob)
    ref = FileRef(
        "doc/Техническое задание.txt",
        "http://x/doc.7z#doc/Техническое задание.txt",
    )
    assert extract_text(ref) == "текст ТЗ"


def test_extract_from_7z_finds_tz_by_name(monkeypatch) -> None:
    """Без внутреннего пути в URL ищем запись с маркером ТЗ по имени."""
    blob = _make_7z(
        {
            "doc/Техническое задание.txt": "текст ТЗ".encode(),
            "doc/приложение.pdf": b"pdf",
        }
    )
    _patch_download(monkeypatch, blob)
    ref = FileRef("Закупочная_документация.7z", "http://x/doc.7z")
    assert extract_text(ref) == "текст ТЗ"
