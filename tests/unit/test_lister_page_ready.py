"""Unit-тесты готовности списка закупок (``lister/page.py::_settle_after_navigation``).

Раньше здесь была фиксированная пауза (``SETTLE_MS``): под параллельным обходом
нескольких площадок в одном процессе рендер SPA мог не успеть за фиксированное
время — контейнеры списка уже в DOM, но их содержимое (ссылка на детали) ещё не
подтянулось. Вместо паузы — опрос первого контейнера до появления ссылки на
детали, с потолком ожидания (см. ``_READY_MAX_MS``).
"""

from __future__ import annotations

from typing import cast

from playwright.async_api import Page

from zakupki_parser.config.models import DomDetailConfig, DomListConfig, PlatformDom
from zakupki_parser.parser.lister.page import (
    _READY_MAX_MS,
    _READY_POLL_MS,
    _settle_after_navigation,
)


def _as_page(obj: object) -> Page:
    return cast(Page, obj)


def _platform() -> PlatformDom:
    return PlatformDom(
        name="test",
        url="https://platform.example",
        list_path="/list",
        list_config=DomListConfig(container=".c", detail_link="a.d", next_page="a.next"),
        detail=DomDetailConfig(),
    )


class _FakeInnerLocator:
    """Ссылка на детали внутри контейнера: готова после ``ready_after`` опросов."""

    def __init__(self, ready_after: int) -> None:
        self._ready_after = ready_after
        self.polls = 0

    async def count(self) -> int:
        self.polls += 1
        return 1 if self.polls > self._ready_after else 0


class _FakeContainer:
    def __init__(self, container_count: int, inner: _FakeInnerLocator) -> None:
        self._container_count = container_count
        self._inner = inner

    @property
    def first(self) -> _FakeContainer:
        return self

    async def count(self) -> int:
        return self._container_count

    def locator(self, selector: str) -> _FakeInnerLocator:
        return self._inner


class _FakePage:
    def __init__(self, container: _FakeContainer) -> None:
        self._container = container
        self.sleeps: list[int] = []

    def locator(self, selector: str) -> _FakeContainer:
        return self._container

    async def wait_for_timeout(self, ms: int) -> None:
        self.sleeps.append(ms)


async def test_returns_immediately_when_no_containers() -> None:
    """Страница без результатов — контейнеров нет, ждать нечего."""
    inner = _FakeInnerLocator(ready_after=0)
    container = _FakeContainer(container_count=0, inner=inner)
    page = _FakePage(container)
    await _settle_after_navigation(_as_page(page), _platform())
    # Один начальный грейс-период + один опрос count()==0 -> выход.
    assert len(page.sleeps) == 1


async def test_waits_until_detail_link_appears() -> None:
    """Ссылка на детали появляется не сразу — опрос дожидается, не выходит раньше."""
    inner = _FakeInnerLocator(ready_after=3)
    container = _FakeContainer(container_count=1, inner=inner)
    page = _FakePage(container)
    await _settle_after_navigation(_as_page(page), _platform())
    # 1 начальный + N опросов до готовности (не исчерпывает весь бюджет).
    assert 1 < len(page.sleeps) < (_READY_MAX_MS // _READY_POLL_MS)
    assert inner.polls > 3


async def test_gives_up_after_budget_without_raising() -> None:
    """Ссылка так и не появилась — по истечении бюджета читаем как есть, не падаем."""
    inner = _FakeInnerLocator(ready_after=10**6)
    container = _FakeContainer(container_count=1, inner=inner)
    page = _FakePage(container)
    await _settle_after_navigation(_as_page(page), _platform())
    total_ms = sum(page.sleeps)
    assert total_ms >= _READY_MAX_MS
