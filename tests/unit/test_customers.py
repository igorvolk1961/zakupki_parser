"""Unit-тесты нормализации заказчиков и извлечения ИНН (ADR-4)."""

from __future__ import annotations

from zakupki_parser.storage.customers import extract_inn_from_link, normalize_name


class TestNormalizeName:
    def test_basic_casefold_and_trim(self) -> None:
        assert normalize_name("  ООО Ромашка  ") == "ооо ромашка"

    def test_collapse_internal_whitespace(self) -> None:
        assert normalize_name("ООО   Ромашка  Плюс") == "ооо ромашка плюс"

    def test_cyrillic_upper_lower(self) -> None:
        assert normalize_name("МУНИЦИПАЛЬНОЕ БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ") == (
            "муниципальное бюджетное учреждение"
        )

    def test_conservative_does_not_merge_distinct(self) -> None:
        # Разные организации не должны сливаться.
        assert normalize_name("ООО Ромашка") != normalize_name("ООО Ромашка Сервис")

    def test_none_and_empty(self) -> None:
        assert normalize_name(None) == ""
        assert normalize_name("") == ""
        assert normalize_name("   ") == ""

    def test_tabs_and_newlines_collapsed(self) -> None:
        assert normalize_name("ООО\tРомашка\nПлюс") == "ооо ромашка плюс"


class TestExtractInnFromLink:
    def test_from_223_link(self) -> None:
        url = "https://zakupki.gov.ru/epz/organization/view223/info.html?&inn=3903007130&kpp=390601001&ogrn=1023900764832"
        assert extract_inn_from_link(url) == "3903007130"

    def test_short_inn(self) -> None:
        assert extract_inn_from_link("?inn=7712345678&x=1") == "7712345678"

    def test_none_when_absent(self) -> None:
        url = "https://zakupki.gov.ru/...?organizationCode=01381000031"
        assert extract_inn_from_link(url) is None
        assert extract_inn_from_link(None) is None
        assert extract_inn_from_link("") is None
