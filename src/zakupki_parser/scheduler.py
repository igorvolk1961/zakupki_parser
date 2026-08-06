"""Таймерный цикл запуска парсера по списку сайтов из ``config_service.yaml``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom
from zakupki_parser.logging_conf import setup_logging
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.orchestrator import Orchestrator
from zakupki_parser.scoring import ExternalScoreClient
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)

_SCORE_PAYLOAD_FIELDS = (
    "number",
    "source_platform",
    "url",
    "customer",
    "law",
    "subject",
    "nmck",
    "publication_date",
    "update_date",
    "deadline",
    "execution_term",
    "security_amount",
    "security_amount_unit",
    "advance",
    "okpd2_codes",
    "kpgz_codes",
    "technical_spec_url",
    "technical_spec_name",
    "detail_json",
    "files_json",
)


def _row_payload(row: Any) -> dict[str, Any]:
    """Все характеристики закупки (для внешнего сервиса скоринга)."""
    result: dict[str, Any] = {}
    for k in _SCORE_PAYLOAD_FIELDS:
        if k == "customer":
            rel = getattr(row, "customer_rel", None)
            value = rel.name if rel is not None else None
        else:
            value = getattr(row, k, None)
        if value is not None:
            result[k] = value
    return result


class Scheduler:
    """Периодически запускает парсинг каждой площадки из списка сайтов."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._stop = asyncio.Event()

        self._db = Database(cfg.service.db)
        self._repository = ProcurementRepository(self._db)
        self._notifier = Notifier(cfg.service.notifications)
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
        setup_logging(self._cfg.logging)
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
        """Бесконечный цикл: проход -> воркер скоринга -> ожидание таймера."""
        await self.start()
        try:
            while not self._stop.is_set():
                await self.run_once()
                await self.run_scoring_worker()
                logger.info("Цикл завершён, ожидание %d с", self._cfg.service.timeout_seconds)
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._cfg.service.timeout_seconds
                    )
                except TimeoutError:
                    continue
        finally:
            await self.stop()

    async def run_scoring_worker(self) -> None:
        """Воркер внешнего скоринга (метод external + режим worker).

        Пробегает по записям со score_method=default, ставит score_method=calculating
        (чтобы не вызывать внешний сервис повторно), вызывает внешний сервис
        и обновляет score (score_method=external).
        """
        cfg = self._cfg.score
        if cfg.method != "external" or cfg.external_call_mode != "worker":
            return
        if not cfg.external_service_url:
            logger.warning("Воркер скоринга: external_service_url не задан")
            return
        client = ExternalScoreClient(cfg)
        batch = 50
        while True:
            rows = await self._repository.list_for_scoring("default", limit=batch)
            if not rows:
                break
            for row in rows:
                await self._repository.set_score_method(row.id, "calculating")
                try:
                    value = await client.score(_row_payload(row))
                    await self._repository.update_score(row.id, value, "external")
                except Exception as exc:  # noqa: BLE001
                    logger.error("Ошибка внешнего скоринга заявки %s: %s", row.id, exc)

    async def _parse_platform(self, platform_id: str, platform: PlatformDom) -> None:
        browser = BrowserManager(self._cfg.parser.browser)
        try:
            await browser.start()
            page = await browser.new_page()
            orchestrator = Orchestrator(
                cfg=self._cfg,
                platform_id=platform_id,
                platform=platform,
                delayer=Delayer(self._cfg.parser.browser),
                repository=self._repository,
                notifier=self._notifier,
                site_cb=self._site_cb,
                db_cb=self._db_cb,
                new_page=browser.new_page,
            )
            try:
                await orchestrator.run(page)
            except CircuitOpenError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Ошибка обработки площадки (сайт недоступен/изменился и т.п.) —
                # учитываем в circuit breaker'е сайта для graceful degradation.
                self._site_cb.record_failure()
                logger.error("Ошибка парсинга площадки %s: %s", platform_id, exc)
                raise
            finally:
                await browser.save_session()
        finally:
            await browser.close()
