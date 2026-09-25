"""Очередь сборов сайтов-источников (``sources.manager``) — без БД и браузера."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from scoring_common.sources import store
from zakupki_parser.config.models import SiteSourcesConfig
from zakupki_parser.net_safety import UnsafeUrlError
from zakupki_parser.sources.crawler import NextStep
from zakupki_parser.sources.manager import SourceCrawlManager
from zakupki_parser.storage.db import SiteSource


class _Repo:
    """Репозиторий site_sources в памяти (те же методы, что SiteSourceMixin)."""

    def __init__(self) -> None:
        self.rows: dict[int, SiteSource] = {}
        self.progress_log: list[dict[str, Any]] = []

    async def get_site_source(self, source_id: int) -> SiteSource | None:
        return self.rows.get(source_id)

    async def get_or_create_site_source(self, url: str, url_norm: str) -> SiteSource:
        for row in self.rows.values():
            if row.url_norm == url_norm:
                return row
        row = SiteSource(
            id=len(self.rows) + 1,
            url=url,
            url_norm=url_norm,
            status="pending",
            pages=0,
            text_chars=0,
            progress={},
            cancel_requested=False,
            text_complete=False,
        )
        self.rows[row.id] = row
        return row

    async def mark_site_source_pending(self, source_id: int) -> None:
        row = self.rows[source_id]
        row.status, row.cancel_requested, row.error = "pending", False, None

    async def mark_site_source_running(self, source_id: int) -> None:
        self.rows[source_id].status = "running"

    async def update_site_source_progress(self, source_id: int, progress: dict[str, Any]) -> None:
        self.rows[source_id].progress = progress
        self.progress_log.append(progress)

    async def finish_site_source(
        self,
        source_id: int,
        *,
        status: str,
        stop_reason: str | None,
        pages: int | None = None,
        text_chars: int | None = None,
        error: str | None = None,
        fetched: bool = False,
    ) -> None:
        row = self.rows[source_id]
        row.status, row.stop_reason, row.error = status, stop_reason, error
        if pages is not None:
            row.pages = pages
        if text_chars is not None:
            row.text_chars = text_chars
        if fetched:
            row.fetched_at = datetime.now(UTC)
            row.text_complete = status == "complete"

    async def request_site_source_cancel(self, source_id: int) -> None:
        self.rows[source_id].cancel_requested = True

    async def site_source_cancel_requested(self, source_id: int) -> bool:
        return bool(self.rows[source_id].cancel_requested)

    async def recover_site_sources_after_restart(self) -> list[int]:
        return [i for i, r in self.rows.items() if r.status == "pending"]


class _Driver:
    """Сайт из ``pages`` страниц, переход — кликом по номеру."""

    active = 0
    max_active = 0

    def __init__(
        self, pages: list[str], *, fail: bool = False, gate: asyncio.Event | None = None
    ) -> None:
        self.pages, self.fail, self.gate, self.i = pages, fail, gate, 0

    async def __aenter__(self) -> _Driver:
        _Driver.active += 1
        _Driver.max_active = max(_Driver.max_active, _Driver.active)
        return self

    async def __aexit__(self, *exc: object) -> None:
        _Driver.active -= 1

    async def open(self, url: str) -> None:
        if self.fail:
            raise RuntimeError("сайт не открылся")
        if self.gate is not None:
            await self.gate.wait()

    async def text(self) -> str:
        return self.pages[self.i]

    async def fingerprint(self) -> str:
        return self.pages[self.i]

    async def current_url(self) -> str:
        return f"https://x.ru/p{self.i + 1}"

    async def find_next(self, page_no: int) -> NextStep | None:
        return NextStep(mode="number") if self.i + 1 < len(self.pages) else None

    async def click_next(self) -> None:
        await asyncio.sleep(0)
        self.i += 1

    async def scroll_to_bottom(self) -> None:
        return None

    async def wait_change(self, old: str, timeout_s: float) -> str | None:
        return None if self.pages[self.i] == old else self.pages[self.i]


async def _any_url(url: str) -> None:
    return None


def _cfg(**kw: Any) -> SiteSourcesConfig:
    return SiteSourcesConfig(delay_ms=(0, 0), **kw)


def _pages(n: int) -> list[str]:
    return [f"Шапка\nкод {i}\nПодвал" for i in range(n)]


async def _drain(manager: SourceCrawlManager) -> None:
    await asyncio.gather(*manager._tasks.values())  # noqa: SLF001


@pytest.fixture(autouse=True)
def _reset_driver_counters() -> None:
    _Driver.active = 0
    _Driver.max_active = 0


def test_ensure_crawls_stores_text_and_first_page() -> None:
    async def run() -> None:
        repo = _Repo()
        manager = SourceCrawlManager(repo, _cfg(), lambda: _Driver(_pages(3)), check_url=_any_url)
        source = await manager.ensure("https://X.ru/list/")
        await _drain(manager)
        row = repo.rows[source.id]
        assert (row.status, row.stop_reason, row.pages) == ("complete", "no_next", 3)
        assert row.text_complete is True
        full = store.get_text(store.text_key("https://x.ru/list"))
        assert full is not None
        # Общая шапка/подвал убраны, маркеры страниц на месте.
        assert [t for _, t in store.split_pages(full)] == ["код 0", "код 1", "код 2"]
        assert store.get_text(store.first_page_key("https://x.ru/list")) == _pages(3)[0]
        assert [p["pages"] for p in repo.progress_log] == [1, 2, 3]

    asyncio.run(run())


def test_fresh_source_not_recrawled_stale_is() -> None:
    async def run() -> None:
        repo = _Repo()
        manager = SourceCrawlManager(repo, _cfg(), lambda: _Driver(_pages(1)), check_url=_any_url)
        source = await manager.ensure("https://x.ru")
        await _drain(manager)
        assert not manager.is_active(source.id)
        await manager.ensure("https://x.ru/")  # тот же URL после нормализации
        assert not manager.is_active(source.id)
        repo.rows[source.id].fetched_at = datetime.now(UTC) - timedelta(days=31)
        await manager.ensure("https://x.ru")
        assert manager.is_active(source.id)
        await _drain(manager)

    asyncio.run(run())


def test_incomplete_recrawl_does_not_replace_complete_text() -> None:
    async def run() -> None:
        repo = _Repo()
        driver_pages = {"n": 3}
        manager = SourceCrawlManager(
            repo,
            _cfg(max_pages=2),
            lambda: _Driver(_pages(driver_pages["n"])),
            check_url=_any_url,
        )
        source = await manager.ensure("https://x.ru")  # 3 страницы, лимит 2 -> неполный
        await _drain(manager)
        assert repo.rows[source.id].status == "incomplete"
        assert repo.rows[source.id].stop_reason == "page_limit"
        # Неполный текст всё же записан — полного ещё не было.
        assert repo.rows[source.id].fetched_at is not None

        driver_pages["n"] = 2
        await manager.refresh(source.id)  # теперь полный
        await _drain(manager)
        complete_text = store.get_text(store.text_key("https://x.ru"))
        assert repo.rows[source.id].text_complete is True

        driver_pages["n"] = 5
        await manager.refresh(source.id)  # снова упёрлись в лимит
        await _drain(manager)
        assert repo.rows[source.id].status == "incomplete"
        assert repo.rows[source.id].text_complete is True
        assert store.get_text(store.text_key("https://x.ru")) == complete_text

    asyncio.run(run())


def test_failed_open_marks_failed_with_error() -> None:
    async def run() -> None:
        repo = _Repo()
        manager = SourceCrawlManager(
            repo, _cfg(), lambda: _Driver(_pages(1), fail=True), check_url=_any_url
        )
        source = await manager.ensure("https://x.ru")
        await _drain(manager)
        row = repo.rows[source.id]
        assert row.status == "failed"
        assert "не открылся" in (row.error or "")
        assert row.fetched_at is None

    asyncio.run(run())


def test_unsafe_url_rejected() -> None:
    async def reject(url: str) -> None:
        raise UnsafeUrlError("внутренний адрес")

    async def run() -> None:
        manager = SourceCrawlManager(_Repo(), _cfg(), lambda: _Driver([]), check_url=reject)
        with pytest.raises(UnsafeUrlError):
            await manager.ensure("http://127.0.0.1/")

    asyncio.run(run())


def test_one_crawl_per_host_at_a_time() -> None:
    async def run() -> None:
        repo = _Repo()
        manager = SourceCrawlManager(
            repo, _cfg(max_concurrent=5), lambda: _Driver(_pages(3)), check_url=_any_url
        )
        await manager.ensure("https://x.ru/a")
        await manager.ensure("https://x.ru/b")
        await _drain(manager)
        assert _Driver.max_active == 1

    asyncio.run(run())


def test_cancel_keeps_collected_as_incomplete() -> None:
    async def run() -> None:
        repo = _Repo()
        gate = asyncio.Event()
        manager = SourceCrawlManager(
            repo, _cfg(), lambda: _Driver(_pages(5), gate=gate), check_url=_any_url
        )
        source = await manager.ensure("https://x.ru")
        await asyncio.sleep(0.01)
        await manager.cancel(source.id)
        gate.set()
        await _drain(manager)
        row = repo.rows[source.id]
        assert row.status == "incomplete"
        assert row.stop_reason == "cancelled"
        assert row.pages == 1

    asyncio.run(run())


def test_recover_restarts_pending() -> None:
    async def run() -> None:
        repo = _Repo()
        row = await repo.get_or_create_site_source("https://x.ru", "https://x.ru")
        manager = SourceCrawlManager(repo, _cfg(), lambda: _Driver(_pages(1)), check_url=_any_url)
        await manager.recover()
        await _drain(manager)
        assert repo.rows[row.id].status == "complete"

    asyncio.run(run())
