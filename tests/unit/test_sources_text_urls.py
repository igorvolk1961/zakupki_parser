"""Нормализация URL источника и удаление общей шапки/подвала страниц."""

from __future__ import annotations

import pytest

from scoring_common.sources.urls import normalize_source_url
from zakupki_parser.sources.text import strip_common_edges


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "HTTPS://OnlineEcology.com/org/ooo-ekopattern/",
            "https://onlineecology.com/org/ooo-ekopattern",
        ),
        ("https://x.ru:443/a", "https://x.ru/a"),
        ("http://x.ru:8080/a", "http://x.ru:8080/a"),
        ("https://x.ru/a#tab", "https://x.ru/a"),
        ("https://x.ru/a?utm_source=y&id=5&gclid=z", "https://x.ru/a?id=5"),
        ("  https://x.ru/  ", "https://x.ru"),
        ("https://x.ru/A/B", "https://x.ru/A/B"),
    ],
)
def test_normalize_source_url(url: str, expected: str) -> None:
    assert normalize_source_url(url) == expected


def _page(rows: list[str]) -> str:
    header = ["Онлайн Экология", "Фильтр: Сбор Транспортирование Утилизация"]
    footer = ["© 2026", "Политика cookies"]
    return "\n".join([*header, *rows, *footer])


def test_strip_common_header_and_footer() -> None:
    pages = [
        _page(["1 11 010 21 49 2", "Сбор (1)"]),
        _page(["4 71 101 01 52 1", "Сбор (1)"]),
        _page(["7 33 100 01 72 4", "Утилизация (1)"]),
    ]
    stripped = strip_common_edges(pages)
    assert stripped[0] == "1 11 010 21 49 2\nСбор (1)"
    assert stripped[2] == "7 33 100 01 72 4\nУтилизация (1)"
    assert all("Фильтр" not in p and "cookies" not in p for p in stripped)


def test_repeated_lines_inside_data_are_kept() -> None:
    """«Сбор (1)» есть на каждой странице, но не на краю — это данные."""
    pages = [_page([f"код {i}", "Сбор (1)", f"адрес {i}"]) for i in range(3)]
    assert all("Сбор (1)" in p for p in strip_common_edges(pages))


def test_fewer_than_three_pages_unchanged() -> None:
    pages = [_page(["a"]), _page(["b"])]
    assert strip_common_edges(pages) == pages


def test_identical_page_is_not_emptied() -> None:
    pages = [_page(["a"]), _page(["b"]), _page([])]
    stripped = strip_common_edges(pages)
    assert stripped[:2] == ["a", "b"]
    assert stripped[2] == pages[2]
