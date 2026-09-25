"""Очередь сборов сайтов-источников в API-процессе.

Сбор идёт фоновой задачей (сайт в 237 страниц — несколько минут): не больше
``max_concurrent`` одновременно и не больше одного на хост. Ход сбора пишется
в ``site_sources.progress`` после каждой страницы — пользователь видит, что
происходит (``GET /api/sources/{id}``).

Текст, полученный полным сбором, не заменяется текстом неполного пересбора
(лимит, отмена, сбой перехода): полный текст достовернее.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

from scoring_common.sources.store import first_page_key, join_pages, put_text, text_key
from scoring_common.sources.urls import normalize_source_url
from zakupki_parser.config.models import SiteSourcesConfig
from zakupki_parser.net_safety import ensure_public_url
from zakupki_parser.sources.crawler import CrawlLimits, CrawlProgress, PageDriver, crawl
from zakupki_parser.sources.text import strip_common_edges
from zakupki_parser.storage.db import SiteSource

logger = logging.getLogger(__name__)

DriverFactory = Callable[[], AbstractAsyncContextManager[PageDriver]]


class SiteSourceStore(Protocol):
    """Нужные менеджеру операции ``site_sources`` (``SiteSourceMixin``)."""

    async def get_site_source(self, source_id: int) -> SiteSource | None: ...

    async def get_or_create_site_source(self, url: str, url_norm: str) -> SiteSource: ...

    async def mark_site_source_pending(self, source_id: int) -> None: ...

    async def mark_site_source_running(self, source_id: int) -> None: ...

    async def update_site_source_progress(
        self, source_id: int, progress: dict[str, Any]
    ) -> None: ...

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
    ) -> None: ...

    async def request_site_source_cancel(self, source_id: int) -> None: ...

    async def site_source_cancel_requested(self, source_id: int) -> bool: ...

    async def recover_site_sources_after_restart(self) -> list[int]: ...


class SourceCrawlManager:
    """Ставит сборы сайтов в очередь и ведёт их до итога."""

    def __init__(
        self,
        repo: SiteSourceStore,
        cfg: SiteSourcesConfig,
        driver_factory: DriverFactory,
        *,
        on_change: Callable[[], Awaitable[None]] | None = None,
        on_finished: Callable[[str], Awaitable[None]] | None = None,
        check_url: Callable[[str], Awaitable[None]] = ensure_public_url,
    ) -> None:
        self._repo = repo
        self._cfg = cfg
        self._driver_factory = driver_factory
        self._on_change = on_change
        # Сбор закончился (любым итогом) — url_norm: пересчёт условий профилей.
        self._on_finished = on_finished
        self._check_url = check_url
        self._sem = asyncio.Semaphore(cfg.max_concurrent)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def is_active(self, source_id: int) -> bool:
        task = self._tasks.get(source_id)
        return task is not None and not task.done()

    def _stale(self, source: SiteSource) -> bool:
        if source.fetched_at is None:
            return True
        return datetime.now(UTC) - source.fetched_at > timedelta(days=self._cfg.ttl_days)

    async def ensure(self, url: str) -> SiteSource:
        """Источник по URL; сбор ставится, если текста нет или он устарел.

        Raises:
            UnsafeUrlError: URL не http/https или ведёт на внутренний адрес.
        """
        await self._check_url(url)
        source = await self._repo.get_or_create_site_source(url.strip(), normalize_source_url(url))
        if self.is_active(source.id):
            return source
        orphaned = source.status in ("pending", "running")
        if orphaned or self._stale(source):
            return await self._start(source)
        return source

    async def refresh(self, source_id: int) -> SiteSource | None:
        """Пересобрать сейчас (если уже не собирается)."""
        source = await self._repo.get_site_source(source_id)
        if source is None or self.is_active(source_id):
            return source
        return await self._start(source)

    async def cancel(self, source_id: int) -> None:
        """Остановить сбор: собранное сохраняется как неполное (``cancelled``)."""
        if self.is_active(source_id):
            await self._repo.request_site_source_cancel(source_id)

    async def recover(self) -> None:
        """После рестарта: прерванные сборы — ``failed``, ожидавшие — заново."""
        for source_id in await self._repo.recover_site_sources_after_restart():
            source = await self._repo.get_site_source(source_id)
            if source is not None:
                await self._start(source)

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in list(self._tasks.values()):
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def _start(self, source: SiteSource) -> SiteSource:
        await self._repo.mark_site_source_pending(source.id)
        self._tasks[source.id] = asyncio.create_task(self._run(source.id, source.url))
        refreshed = await self._repo.get_site_source(source.id)
        return refreshed or source

    def _limits(self) -> CrawlLimits:
        low, high = self._cfg.delay_ms
        return CrawlLimits(
            max_pages=self._cfg.max_pages,
            max_chars=int(self._cfg.max_text_mb * 1_000_000),
            page_timeout_s=self._cfg.page_timeout_s,
            total_timeout_s=self._cfg.total_timeout_min * 60,
            delay_ms=(low, high),
        )

    async def _run(self, source_id: int, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with self._sem, lock:
            await self._repo.mark_site_source_running(source_id)
            await self._notify()
            try:
                await self._crawl(source_id, url)
            except asyncio.CancelledError:
                await self._repo.finish_site_source(
                    source_id, status="failed", stop_reason="cancelled", error="сервис остановлен"
                )
                raise
            except Exception as exc:  # noqa: BLE001 — сбор одного сайта не роняет API
                logger.warning("Сбор сайта %s не выполнен: %s", url, exc)
                await self._repo.finish_site_source(
                    source_id, status="failed", stop_reason="error", error=str(exc)[:1000]
                )
            await self._notify()
            if self._on_finished is not None:
                try:
                    await self._on_finished(normalize_source_url(url))
                except Exception:  # noqa: BLE001
                    logger.warning("Пересчёт после сбора сайта %s не запущен", url, exc_info=True)

    async def _crawl(self, source_id: int, url: str) -> None:
        await self._check_url(url)

        async def progress(p: CrawlProgress) -> None:
            await self._repo.update_site_source_progress(
                source_id,
                {
                    "pages": p.pages,
                    "chars": p.chars,
                    "current_url": p.current_url,
                    "mode": p.mode,
                    "elapsed_s": round(p.elapsed_s, 1),
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )

        async def cancelled() -> bool:
            return bool(await self._repo.site_source_cancel_requested(source_id))

        async with self._driver_factory() as driver:
            outcome = await crawl(
                driver, url, self._limits(), on_page=progress, cancelled=cancelled
            )

        urls = [u for u, _ in outcome.pages]
        texts = [t for _, t in outcome.pages]
        # В режимах «показать ещё»/прокрутки страницы — приросты одной страницы:
        # общей шапки у них нет.
        if outcome.mode not in ("load_more", "scroll"):
            texts = strip_common_edges(texts)
        status = "complete" if outcome.complete else "incomplete"
        current = await self._repo.get_site_source(source_id)
        replace = outcome.complete or not (current is not None and current.text_complete)
        if replace:
            url_norm = normalize_source_url(url)
            full = join_pages(zip(urls, texts, strict=True))
            await asyncio.to_thread(put_text, text_key(url_norm), full)
            await asyncio.to_thread(put_text, first_page_key(url_norm), outcome.pages[0][1])
        logger.info(
            "Сайт %s собран: %s, %d стр., %d символов, остановка: %s",
            url,
            status,
            len(outcome.pages),
            outcome.chars,
            outcome.stop_reason,
        )
        await self._repo.finish_site_source(
            source_id,
            status=status,
            stop_reason=outcome.stop_reason,
            pages=len(outcome.pages) if replace else None,
            text_chars=sum(len(t) for t in texts) if replace else None,
            error=outcome.error,
            fetched=replace,
        )

    async def _notify(self) -> None:
        if self._on_change is not None:
            try:
                await self._on_change()
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось оповестить о смене статуса источника", exc_info=True)
