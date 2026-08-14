"""Тесты поиска и извлечения текста ТЗ (requires_tz_review)."""

from __future__ import annotations

import io
import zipfile

from scoring_service.pipeline.tz_review import (
    _decode_member_name,
    clean_text,
    collect_files,
    find_tz_file,
    find_tz_reference,
    is_tz,
)


def _file(name: str, url: str = "http://x/f") -> dict[str, str]:
    return {"name": name, "url": url}


def test_is_tz_matches_full_phrase() -> None:
    assert is_tz("техническое задание.pdf")
    assert is_tz("техническоезадание.docx")
    assert is_tz("тех.задание.docx")


def test_is_tz_abbreviation_with_suffix() -> None:
    assert is_tz("тз_2.docx")
    assert is_tz("aaa_тз_2.docx")
    assert is_tz("ТЗ_2024.pdf")
    assert is_tz("123_тз-1.docx")


def test_is_tz_rejects_partial_word() -> None:
    assert not is_tz("втзд.docx")
    assert not is_tz("сметатз.docx")
    assert not is_tz("объектов.docx")


def test_find_tz_file_priority() -> None:
    record = {
        "files_json": [
            _file("приложение_2.docx"),
            _file("техническое задание.pdf"),
        ]
    }
    ref = find_tz_file(record)
    assert ref is not None
    assert ref.name == "техническое задание.pdf"


def test_find_tz_uses_files_json() -> None:
    record = {
        "files_json": [
            {"name": "ТЗ_1.pdf", "url": "http://x/tz.pdf"},
            _file("приложение.docx"),
        ]
    }
    ref = find_tz_file(record)
    assert ref is not None
    assert ref.name == "ТЗ_1.pdf"
    assert ref.url == "http://x/tz.pdf"


def test_collect_files_reads_json() -> None:
    record = {
        "files_json": [_file("doc.docx"), _file("doc2.docx")],
    }
    assert len(collect_files(record)) == 2


def test_find_tz_reference_none_when_no_tz() -> None:
    record = {"files_json": [_file("смета.xlsx"), _file("документ.docx")]}
    assert find_tz_reference(record) is None


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("a\t\t b\n\n\n c ") == "a b\n\n c"


def test_clean_text_drops_garbage_lines() -> None:
    base64 = "A" * 400
    out = clean_text(f"норма\n{base64}\nконец")
    assert base64 not in out
    assert "норма" in out
    assert "конец" in out


def _cp437_mangled(name: str, encoding: str = "cp1251") -> str:
    """Имя так, как его вернул бы zipfile без UTF-8-флага (декодирование cp437)."""
    return name.encode(encoding).decode("cp437")


def test_decode_member_name_cp1251() -> None:
    assert _decode_member_name(_cp437_mangled("ТЗ_2.docx")) == "ТЗ_2.docx"
    assert _decode_member_name(_cp437_mangled("техническое задание.pdf")) == (
        "техническое задание.pdf"
    )


def test_decode_member_name_utf8_fallback() -> None:
    assert _decode_member_name("plain.txt") == "plain.txt"


def test_archive_names_decode_cp1251() -> None:
    """В zip с именами в cp1251 (без UTF-8-флага) имя ТЗ корректно распознаётся."""
    inner = _cp437_mangled("Техническое задание.docx")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo(inner)
        zi.flag_bits = 0  # без UTF-8-флага: имя хранится в локальной кодировке
        zf.writestr(zi, "содержимое ТЗ")

    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        names = [_decode_member_name(i.filename) for i in zf.infolist()]

    assert "Техническое задание.docx" in names
    assert is_tz(names[0])
