"""Таймерный цикл запуска парсера по списку сайтов из ``config_service.yaml``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom
from zakupki_parser.logging_conf import setup_logging
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.orchestrator import Orchestrator
from zakupki_parser.parser.orchestrator.context import ProfileRunContext
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class Scheduler:
    """Периодически запускает парсинг каждой площадки из списка сайтов."""

    def __init__(
        self,
        cfg: AppConfig,
        on_update: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._cfg = cfg
        self._stop = asyncio.Event()
        # Колбэк уведомления об изменении данных (например, WebSocket-широковещание).
        self._on_update = on_update

        self._db = Database(cfg.ops.db)
        self._repository = ProcurementRepository(self._db)
        self._notifier = Notifier(cfg.ops.notifications)
        self._site_cb = CircuitBreaker(
            "site",
            cfg.ops.circuit_breaker_failure_threshold,
            cfg.ops.circuit_breaker_reset_timeout_seconds,
        )
        self._db_cb = CircuitBreaker(
            "db",
            cfg.ops.circuit_breaker_failure_threshold,
            cfg.ops.circuit_breaker_reset_timeout_seconds,
        )

    async def start(self) -> None:
        setup_logging(self._cfg.logging)
        await self._db.connect()
        # Активность площадок синхронизируем в БД (источник истины — platforms).
        enabled = {s.platform_id for s in self._cfg.service.sites if s.enabled}
        await self._repository.sync_platform_enabled(enabled)

    async def stop(self) -> None:
        self._stop.set()
        await self._db.dispose()

    async def run_once(self) -> None:
        """Один проход: все включённые профили незаблокированных пользователей.

        Recovery очереди скоринга (догоняем закупки, не попавшие в очередь).
        Порядок циклов — ``profiles_loop_order``:

        - ``platform_then_profile`` (дефолт): снаружи площадка, внутри профили.
          Одинаковые запросы разных профилей к одной площадке объединяются.
        - ``profile_then_platform``: снаружи профиль, внутри площадки (изоляция
          по профилю, цена — потеря переиспользования обходов).
        """
        await self._recover_scoring_queue()
        enabled_platforms = await self._repository.enabled_platform_ids()
        ctxs = await self._gather_profile_ctxs()

        if self._cfg.service.profiles_loop_order == "profile_then_platform":
            for ctx in ctxs:
                for platform_id in self._ordered_platforms_for_profile(ctx, enabled_platforms):
                    await self._process_platform(platform_id, [ctx])
        else:
            for platform_id in self._ordered_enabled_platforms(enabled_platforms):
                batch = [c for c in ctxs if self._profile_on_platform(c, platform_id)]
                if not batch:
                    continue
                await self._process_platform(platform_id, batch)

    def _ordered_enabled_platforms(self, enabled: set[str]) -> list[str]:
        """Активные площадки в порядке config_service.yaml (конфиг — интерфейс)."""
        return [s.platform_id for s in self._cfg.service.sites if s.platform_id in enabled]

    async def _process_platform(self, platform_id: str, profiles: list[ProfileRunContext]) -> None:
        """Обрабатывает одну площадку для набора профилей."""
        platform = self._cfg.dom.platforms.get(platform_id)
        if platform is None:
            logger.warning(
                "platform_id %s отсутствует в config_dom.yaml, пропуск",
                platform_id,
            )
            return
        logger.info("Обработка площадки: %s (профилей: %d)", platform_id, len(profiles))
        try:
            await self._parse_platform(platform_id, platform, profiles)
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка обработки площадки %s: %s", platform_id, exc)
        if self._on_update is not None:
            await self._on_update()

    def _profile_on_platform(self, ctx: ProfileRunContext, platform_id: str) -> bool:
        """True, если профиль относится к площадке (``target_etp`` пуст — все)."""
        etp = set(ctx.profile.target_etp or [])
        return not etp or platform_id in etp

    def _ordered_platforms_for_profile(
        self, ctx: ProfileRunContext, enabled: set[str]
    ) -> list[str]:
        return [
            p for p in self._ordered_enabled_platforms(enabled) if self._profile_on_platform(ctx, p)
        ]

    async def _gather_profile_ctxs(self) -> list[ProfileRunContext]:
        """Включённые профили незаблокированных пользователей + слова (BR-07).

        Пустой список — профилей нет: обходы не строятся (dev-режим).
        """
        if self._repository is None:
            return []
        profiles = await self._repository.list_enabled_profiles_for_active_users()
        if not profiles:
            return []
        kw_map = await self._repository.list_profiles_keywords([p.id for p in profiles])
        return [
            ProfileRunContext(
                profile=p,
                keywords=kw_map.get(p.id, {}).get("keywords", []),
                exclusion_words=kw_map.get(p.id, {}).get("exclusion_words", []),
            )
            for p in profiles
        ]

    async def run_service(self) -> None:
        """Бесконечный цикл: проход -> ожидание таймера."""
        await self.start()
        try:
            while not self._stop.is_set():
                await self.run_once()
                logger.info("Цикл завершён, ожидание %d с", self._cfg.ops.timeout_seconds)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._cfg.ops.timeout_seconds)
                except TimeoutError:
                    continue
        finally:
            await self.stop()

    async def _recover_scoring_queue(self) -> None:
        """Догоняющая постановка закупок в очередь скоринга (после сбоев транспорта).

        Ищет в БД закупки с невыполненным внешним скорингом (``fit_score IS NULL``),
        не поставленные в очередь (``scoring_queued_at IS NULL``), обновлённые
        после постановки либо с меткой постановки старше ``recovery_ttl_seconds``
        (задание потеряно — воркер снял задачу, очередь очищена), и ставит их в
        очередь fit с приоритетом по времени обновления/публикации (новые —
        раньше, ZPOPMAX берёт больший score).

        Идемпотентно: метка проставляется только после успешного enqueue, поэтому
        повторно уже поставленные закупки не дублируются. При первом же сбое
        enqueue (транспорт снова недоступен) recovery прекращается до следующего
        цикла.
        """
        if not self._cfg.score.scoring_transport_url or self._repository is None:
            return
        transport = ScoringTransportClient(
            self._cfg.score.scoring_transport_url,
            auth_token=self._cfg.score.scoring_transport_token,
        )
        now = datetime.now(UTC)
        ttl = self._cfg.score.recovery_ttl_seconds
        queued_before = now - timedelta(seconds=ttl) if ttl > 0 else None
        for _ in range(50):  # не более 50 партий по 200 за цикл
            items = await self._repository.find_unscored(limit=200, queued_before=queued_before)
            if not items:
                return
            for item in items:
                ts = item["update_date"] or item["publication_date"]
                priority = ts.timestamp() if ts is not None else now.timestamp()
                try:
                    await transport.enqueue(item["id"], priority)
                    await self._repository.mark_scoring_queued(item["id"], now)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Recovery очереди скоринга прерван: %s не поставлена (%s)",
                        item["id"],
                        exc,
                    )
                    return
            logger.info(
                "Recovery очереди скоринга: поставлено закупок в очередь: %d",
                len(items),
            )

    async def _parse_platform(
        self,
        platform_id: str,
        platform: PlatformDom,
        profiles: list[ProfileRunContext],
    ) -> None:
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
                on_record_saved=self._on_update,
            )
            try:
                await orchestrator.run(page, profiles=profiles)
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
