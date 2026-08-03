"""Таймерный цикл запуска парсера по списку сайтов из ``config_service.yaml``."""

from __future__ import annotations

import asyncio
import logging

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.circuit import CircuitBreaker
from zakupki_parser.config.models import AppConfig, PlatformDom
from zakupki_parser.file_processor import FileProcessor
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.orchestrator import Orchestrator
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.last_seen import LastSeenStore
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class Scheduler:
    """Периодически запускает парсинг каждой площадки из списка сайтов."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._stop = asyncio.Event()

        base_dir = cfg.configs_dir.parent
        self._db = Database(cfg.service.db)
        self._repository = ProcurementRepository(self._db)
        self._notifier = Notifier(cfg.service.webhook)
        self._file_processor = FileProcessor()
        self._last_seen = LastSeenStore(
            (base_dir / cfg.service.data_dir).resolve(),
            cfg.service.default_cutoff_days,
        )
        self._site_cb = CircuitBreaker(
            "site",
            cfg.service.circuit_breaker_failure_threshold,
            cfg.service.circuit_breaker_reset_timeout_seconds,
        )
        self._db_cb = CircuitBreaker(
            "db",
            cfg.service.circuit_breaker_failure_threshold,
            cfg.service.circuit_breaker_reset_timeout_seconds,
        )

    async def start(self) -> None:
        await self._db.connect()

    async def stop(self) -> None:
        self._stop.set()
        await self._db.dispose()

    async def run_once(self) -> None:
        """Один проход по всем включённым площадкам."""
        for entry in self._cfg.service.sites:
            if not entry.enabled:
                continue
            platform = self._cfg.dom.platforms.get(entry.platform_id)
            if platform is None:
                logger.warning(
                    "platform_id %s отсутствует в config_dom.yaml, пропуск",
                    entry.platform_id,
                )
                continue
            logger.info("Обработка площадки: %s", entry.platform_id)
            try:
                await self._parse_platform(entry.platform_id, platform)
            except Exception as exc:  # noqa: BLE001
                logger.error("Ошибка обработки площадки %s: %s", entry.platform_id, exc)

    async def run_service(self) -> None:
        """Бесконечный цикл: проход -> ожидание таймера."""
        await self.start()
        try:
            while not self._stop.is_set():
                await self.run_once()
                logger.info("Цикл завершён, ожидание %d с", self._cfg.service.timeout_seconds)
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._cfg.service.timeout_seconds
                    )
                except TimeoutError:
                    continue
        finally:
            await self.stop()

    async def _parse_platform(self, platform_id: str, platform: PlatformDom) -> None:
        browser = BrowserManager(self._cfg.parser.browser)
        try:
            await browser.start()
            page = await browser.new_page()
            orchestrator = Orchestrator(
                cfg=self._cfg,
                platform_id=platform_id,
                platform=platform,
                filters_cfg=self._cfg.filters,
                delayer=Delayer(self._cfg.parser.browser),
                repository=self._repository,
                notifier=self._notifier,
                file_processor=self._file_processor,
                last_seen=self._last_seen,
                site_cb=self._site_cb,
                db_cb=self._db_cb,
            )
            try:
                await orchestrator.run(page)
            finally:
                await browser.save_session()
        finally:
            await browser.close()
