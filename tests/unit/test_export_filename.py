"""Тесты имён файлов экспорта профиля: только латиница (R8, экспорт профиля)."""

from __future__ import annotations

from zakupki_parser.api.app.routes.clients import _safe_filename, _transliterate


def test_safe_filename_latin_only() -> None:
    """Имя файла из кириллического имени профиля — только латиница."""

    assert _safe_filename("Медицинские технологии") == "Meditsinskie_tehnologii"
    assert _safe_filename("ООО «Ромашка»") == "OOO_Romashka"


def test_safe_filename_ascii_passthrough() -> None:
    """Латиница и цифры сохраняются; недопустимые символы — в подчёркивания."""

    assert _safe_filename("BBK IT") == "BBK_IT"
    assert _safe_filename("export-me") == "export-me"
    assert _safe_filename("a/b\\c:d?e") == "a_b_c_d_e"


def test_transliterate_case() -> None:
    """Транслитерация сохраняет регистр и верхний/нижний регистр кириллицы."""

    assert _transliterate("Ёлка ёлка") == "Elka elka"
    assert _transliterate("йЙ") == "yY"


def test_safe_filename_empty_fallback() -> None:
    """Пустое/полностью не-ASCII имя падает на дефолт 'profile'."""

    assert _safe_filename("") == "profile"
    assert _safe_filename("   ") == "profile"
