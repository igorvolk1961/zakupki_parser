"""Цикл обхода сайта по пагинации (``sources.crawler``) — без браузера.

Поддельный драйвер описывает сайт как список «состояний» страницы; переход
(клик/ссылка/прокрутка) переводит в следующее состояние. Так проверяются все
причины остановки, накопление текста и лимиты.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from zakupki_parser.sources.crawler import (
    CrawlLimits,
    CrawlOutcome,
    CrawlProgress,
    NextStep,
    crawl,
    increment,
    next_url_by_param,
)


@dataclass
class _Site:
    """Сайт: тексты страниц и способ перехода с каждой из них."""

    texts: list[str]
    # Шаг перехода со страницы i (None — перехода нет).
    steps: list[NextStep | None]
    # Переход с последней страницы ведёт на эту (повтор) — None: переход «не меняет».
    wrap_to: int | None = None
    fail_on_click: int | None = None
    urls: list[str] = field(default_factory=list)


class _FakeDriver:
    def __init__(self, site: _Site) -> None:
        self.site = site
        self.index = 0
        self.opened: list[str] = []

    def _url(self) -> str:
        return self.site.urls[self.index] if self.site.urls else f"http://x/{self.index + 1}"

    async def open(self, url: str) -> None:
        self.opened.append(url)
        if self.site.urls and url in self.site.urls:
            self.index = self.site.urls.index(url)
        elif len(self.opened) > 1:
            self._advance()

    def _advance(self) -> None:
        if self.index + 1 < len(self.site.texts):
            self.index += 1
        elif self.site.wrap_to is not None:
            self.index = self.site.wrap_to

    async def text(self) -> str:
        return self.site.texts[self.index]

    async def fingerprint(self) -> str:
        return self.site.texts[self.index]

    async def current_url(self) -> str:
        return self._url()

    async def find_next(self, page_no: int) -> NextStep | None:
        return self.site.steps[self.index] if self.index < len(self.site.steps) else None

    async def click_next(self) -> None:
        if self.site.fail_on_click == self.index:
            raise RuntimeError("элемент перекрыт")
        self._advance()

    async def scroll_to_bottom(self) -> None:
        self._advance()

    async def wait_change(self, old_fingerprint: str, timeout_s: float) -> str | None:
        current = await self.fingerprint()
        return None if current == old_fingerprint else current


async def _no_sleep(_: float) -> None:
    return None


def _run(
    site: _Site, limits: CrawlLimits | None = None, **kwargs: Any
) -> tuple[CrawlOutcome, _FakeDriver]:
    driver = _FakeDriver(site)
    outcome = asyncio.run(
        crawl(driver, "http://x/1", limits or CrawlLimits(), sleep=_no_sleep, **kwargs)
    )
    return outcome, driver


_CLICK = NextStep(mode="number")


def test_numbered_pagination_until_no_next() -> None:
    site = _Site(texts=["p1", "p2", "p3"], steps=[_CLICK, _CLICK, None])
    outcome, _ = _run(site)
    assert [t for _, t in outcome.pages] == ["p1", "p2", "p3"]
    assert outcome.stop_reason == "no_next"
    assert outcome.complete
    assert outcome.mode == "number"


def test_disabled_next_button_ends_pagination() -> None:
    site = _Site(texts=["p1", "p2"], steps=[_CLICK, NextStep(mode="label", disabled=True)])
    outcome, _ = _run(site)
    assert len(outcome.pages) == 2
    assert outcome.stop_reason == "no_next"


def test_last_page_shown_again_is_repeat() -> None:
    site = _Site(texts=["p1", "p2", "p3"], steps=[_CLICK, _CLICK, _CLICK], wrap_to=1)
    outcome, _ = _run(site)
    assert [t for _, t in outcome.pages] == ["p1", "p2", "p3"]
    assert outcome.stop_reason == "repeat"
    assert outcome.complete


def test_click_without_change_is_no_change() -> None:
    site = _Site(texts=["p1", "p2"], steps=[_CLICK, _CLICK])
    outcome, _ = _run(site)
    assert len(outcome.pages) == 2
    assert outcome.stop_reason == "no_change"
    assert outcome.complete


def test_rel_next_links_are_followed() -> None:
    urls = ["http://x/1", "http://x/2", "http://x/3"]
    steps: list[NextStep | None] = [
        NextStep(mode="rel_next", href=urls[1]),
        NextStep(mode="rel_next", href=urls[2]),
        None,
    ]
    site = _Site(texts=["p1", "p2", "p3"], steps=steps, urls=urls)
    outcome, driver = _run(site)
    assert [u for u, _ in outcome.pages] == urls
    assert driver.opened == ["http://x/1", *urls[1:]]
    assert outcome.stop_reason == "no_next"


def test_url_param_used_when_no_button() -> None:
    urls = ["http://x/list?page=1", "http://x/list?page=2", "http://x/list?page=3"]
    site = _Site(texts=["p1", "p2", "p2"], steps=[None, None, None], urls=urls)
    driver = _FakeDriver(site)
    outcome = asyncio.run(crawl(driver, urls[0], CrawlLimits(), sleep=_no_sleep))
    assert [t for _, t in outcome.pages] == ["p1", "p2"]
    assert outcome.mode == "url_param"
    # Третья страница совпала со второй — сайт за последней отдаёт её же.
    assert outcome.stop_reason == "no_change"
    assert outcome.complete


def test_single_page_site_is_complete() -> None:
    site = _Site(texts=["единственная"], steps=[None])
    outcome, _ = _run(site)
    assert len(outcome.pages) == 1
    assert outcome.stop_reason == "no_next"
    assert outcome.complete


def test_infinite_scroll_keeps_only_increments() -> None:
    site = _Site(texts=["a", "a\nb", "a\nb\nc"], steps=[None, None, None])
    outcome, _ = _run(site)
    assert [t for _, t in outcome.pages] == ["a", "b", "c"]
    assert outcome.mode == "scroll"
    assert outcome.stop_reason == "no_next"


def test_load_more_keeps_only_increments() -> None:
    more = NextStep(mode="load_more")
    site = _Site(texts=["r1", "r1\nr2", "r1\nr2\nr3"], steps=[more, more, None])
    outcome, _ = _run(site)
    assert [t for _, t in outcome.pages] == ["r1", "r2", "r3"]
    assert outcome.mode == "load_more"


def test_page_limit_is_incomplete() -> None:
    site = _Site(texts=[f"p{i}" for i in range(10)], steps=[_CLICK] * 10)
    outcome, _ = _run(site, CrawlLimits(max_pages=3, delay_ms=(0, 0)))
    assert len(outcome.pages) == 3
    assert outcome.stop_reason == "page_limit"
    assert not outcome.complete


def test_size_limit_is_incomplete() -> None:
    site = _Site(texts=["x" * 10 for _ in range(10)], steps=[_CLICK] * 10)
    site.texts = [f"{i}" * 10 for i in range(10)]
    outcome, _ = _run(site, CrawlLimits(max_chars=25, delay_ms=(0, 0)))
    assert outcome.stop_reason == "size_limit"
    assert len(outcome.pages) == 3


def test_time_limit_is_incomplete() -> None:
    """Время вышло, а следующая страница есть — сбор неполный."""
    calls = {"n": 0}

    def clock() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 100.0  # старт, затем «прошло 100 с»

    site = _Site(texts=["p1", "p2", "p3"], steps=[_CLICK, _CLICK, None])
    outcome, _ = _run(site, CrawlLimits(total_timeout_s=10, delay_ms=(0, 0)), clock=clock)
    assert outcome.stop_reason == "time_limit"
    assert len(outcome.pages) == 1
    assert not outcome.complete


def test_limit_not_applied_when_no_next_page() -> None:
    """Сайт ровно в ``max_pages`` страниц собран полностью."""
    site = _Site(texts=["p1", "p2"], steps=[_CLICK, None])
    outcome, _ = _run(site, CrawlLimits(max_pages=2, delay_ms=(0, 0)))
    assert outcome.stop_reason == "no_next"
    assert outcome.complete


def test_cancel_stops_with_collected_pages() -> None:
    calls = {"n": 0}

    async def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    site = _Site(texts=["p1", "p2", "p3"], steps=[_CLICK, _CLICK, None])
    outcome, _ = _run(site, cancelled=cancelled)
    assert len(outcome.pages) == 2
    assert outcome.stop_reason == "cancelled"
    assert not outcome.complete


def test_navigation_failure_keeps_collected_pages() -> None:
    site = _Site(texts=["p1", "p2", "p3"], steps=[_CLICK, _CLICK, None], fail_on_click=1)
    outcome, _ = _run(site)
    assert [t for _, t in outcome.pages] == ["p1", "p2"]
    assert outcome.stop_reason == "nav_failed"
    assert "перекрыт" in (outcome.error or "")
    assert not outcome.complete


def test_progress_reported_after_each_page() -> None:
    seen: list[CrawlProgress] = []

    async def on_page(p: CrawlProgress) -> None:
        seen.append(p)

    site = _Site(texts=["p1", "p22", "p333"], steps=[_CLICK, _CLICK, None])
    _run(site, on_page=on_page)
    assert [p.pages for p in seen] == [1, 2, 3]
    assert [p.chars for p in seen] == [2, 5, 9]
    assert seen[-1].current_url == "http://x/3"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://x/list?page=3", "http://x/list?page=4"),
        ("http://x/list?q=a&p=9", "http://x/list?q=a&p=10"),
        ("http://x/list?PAGEN_1=2", "http://x/list?PAGEN_1=3"),
        ("http://x/list?pageIndex=1&size=20", "http://x/list?pageIndex=2&size=20"),
        ("http://x/list?sort=page", None),
        ("http://x/list", None),
    ],
)
def test_next_url_by_param(url: str, expected: str | None) -> None:
    assert next_url_by_param(url) == expected


def test_load_more_rows_inserted_before_button() -> None:
    """Новые записи появляются перед кнопкой — прирост без повторов."""
    more = NextStep(mode="load_more")
    site = _Site(
        texts=["r1\nПоказать ещё", "r1\nr2\nПоказать ещё", "r1\nr2\nr3"],
        steps=[more, more, None],
    )
    outcome, _ = _run(site)
    assert [t for _, t in outcome.pages] == ["r1\nПоказать ещё", "r2", "r3"]


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ("a\nb", "a\nb\nc", "c"),
        ("a\nMore", "a\nb\nc\nMore", "b\nc"),
        ("a\nb\nMore", "a\nb\nc", "c"),
        ("x", "y", "y"),
    ],
)
def test_increment(previous: str, current: str, expected: str) -> None:
    assert increment(previous, current) == expected
