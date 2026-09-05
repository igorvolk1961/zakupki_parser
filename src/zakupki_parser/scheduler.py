"""Таймерный цикл запуска парсера по списку сайтов из ``config_service.yaml``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom
from zakupki_parser.logging_conf import reset_run_context, set_run_context, setup_logging
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.orchestrator import Orchestrator
from zakupki_parser.parser.orchestrator.context import ProfileRunContext
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.repository import ProcurementRepository
from zakupki_parser.storage.repository.accounts import effective_options

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
        # Номер текущей итерации цикла (run_once): растёт с каждым проходом,
        # записывается в scoring_iteration закупок — граница батча журнала «Метрики».
        self._iteration = 0

    async def start(self) -> None:
        setup_logging(self._cfg.logging)
        await self._db.connect()
        # Активность площадок синхронизируем в БД (источник истины — platforms).
        enabled = {s.platform_id for s in self._cfg.service.sites if s.enabled}
        await self._repository.sync_platform_enabled(enabled)

    async def stop(self) -> None:
        self._stop.set()
        await self._db.dispose()

    async def run_once(self, iteration: int = 0) -> None:
        """Один проход: все включённые площадки обрабатываются параллельно.

        Recovery очереди скоринга (догоняем закупки, не попавшие в очередь) выполняется
        до обхода площадок. Каждая включённая площадка обрабатывается отдельной
        ``asyncio.Task`` с лимитом ``config_parser.max_concurrent_platforms``: задержка/
        backoff/circuit-breaker одной площадки не блокирует остальные. Площадки одного
        домена/бэкенда (``domain_group``) дополнительно ограничены
        ``config_parser.max_concurrent_per_domain`` (44-ФЗ/223-ФЗ одного сайта не идут
        параллельно — общий IP/антибот). Профили
        распределяются по площадкам через ``_profile_on_platform`` (``target_etp``);
        одинаковые обходы (площадка + набор ОКПД2) дедуплицируются ``_build_units``
        (``deduplicate_requests``).
        """
        await self._recover_scoring_queue(iteration)
        enabled_platforms = await self._repository.enabled_platform_ids()
        ctxs = await self._gather_profile_ctxs()

        sem = asyncio.Semaphore(self._cfg.parser.max_concurrent_platforms)
        # Доменный лимит (R5): 44-ФЗ/223-ФЗ одного сайта (одинаковый domain_group
        # или hostname url) не обрабатываются параллельно — общий бэкенд/IP/антибот.
        per_domain_limit = self._cfg.parser.max_concurrent_per_domain
        per_domain: dict[str, asyncio.Semaphore] = {}

        async def _run_platform(platform_id: str, profiles: list[ProfileRunContext]) -> None:
            dkey = self._domain_key(platform_id)
            d_sem = per_domain.setdefault(dkey, asyncio.Semaphore(per_domain_limit))
            # Единый порядок захвата (глобальный -> доменный) исключает deadlock.
            async with sem, d_sem:
                await self._process_platform(platform_id, profiles, iteration)

        pending = []
        for platform_id in self._ordered_enabled_platforms(enabled_platforms):
            batch = [c for c in ctxs if self._profile_on_platform(c, platform_id)]
            if not batch:
                continue
            pending.append(_run_platform(platform_id, batch))

        if pending:
            # return_exceptions=True: ошибка одной площадки не отменяет остальные
            # (ошибка площадки уже изолирована внутри _process_platform; здесь —
            # страховка на случай непредвиденного исключения, напр. в _on_update).
            results = await asyncio.gather(*pending, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logger.error("Параллельная обработка площадки завершилась ошибкой: %s", result)

    def _ordered_enabled_platforms(self, enabled: set[str]) -> list[str]:
        """Активные площадки в порядке config_service.yaml (конфиг — интерфейс)."""
        return [s.platform_id for s in self._cfg.service.sites if s.platform_id in enabled]

    def _domain_key(self, platform_id: str) -> str:
        """Ключ группировки площадок по общему бэкенду/домену.

        Приоритет у явного ``PlatformDom.domain_group`` (надёжен для поддоменов и
        API-хостов, отличающихся от ``url``). Иначе — hostname из ``url``; для
        неизвестного platform_id (тесты/заглушки) — сам platform_id.
        """
        platform = self._cfg.dom.platforms.get(platform_id)
        if platform is None:
            return platform_id
        if platform.domain_group:
            return platform.domain_group
        return urlparse(platform.url).netloc.lower()

    async def _process_platform(
        self, platform_id: str, profiles: list[ProfileRunContext], iteration: int = 0
    ) -> None:
        """Обрабатывает одну площадку для набора профилей."""
        platform = self._cfg.dom.platforms.get(platform_id)
        if platform is None:
            logger.warning(
                "platform_id %s отсутствует в config_dom.yaml, пропуск",
                platform_id,
            )
            return
        logger.info(
            "Обработка площадки: %s (профилей: %d, итерация: %d)",
            platform_id,
            len(profiles),
            iteration,
        )
        # Контекст для логов: последующие записи этой площадки (и её подзадач)
        # автоматически получают префикс [platform#iteration] (см. logging_filter).
        token = set_run_context(platform_id, iteration)
        try:
            await self._parse_platform(platform_id, platform, profiles, iteration)
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка обработки площадки %s: %s", platform_id, exc)
        finally:
            reset_run_context(token)
        if self._on_update is not None:
            await self._on_update()

    def _profile_on_platform(self, ctx: ProfileRunContext, platform_id: str) -> bool:
        """True, если профиль относится к площадке (``target_etp`` пуст — все)."""
        etp = set(ctx.profile.target_etp or [])
        return not etp or platform_id in etp

    async def _gather_profile_ctxs(self) -> list[ProfileRunContext]:
        """Включённые профили незаблокированных пользователей + слова (BR-07).

        Пустой список — профилей нет: обходы не строятся (dev-режим).
        Из сбора исключаются:
        - пустые/невалидные профили (без компетенций): конвейер не скорит без
          контекста компетенций (BR-07);
        - профили владельцев, которым сейчас недоступна платная опция скоринга
          (``scoring``): сбор закупок в текущей архитектуре всегда приводит к
          LLM-скорингу, а доступность платных операций задаётся аккаунтом/триалом
          пользователя (владелец ограничил операции — тратить деньги на его
          профиль нельзя).
        """
        if self._repository is None:
            return []
        profiles = await self._repository.list_enabled_profiles_for_active_users()
        if not profiles:
            return []
        profiles = [p for p in profiles if self._profile_has_valid_competencies(p)]
        if not profiles:
            return []
        # Доступность опции scoring считаем по пользователям профилей: триал либо
        # активный аккаунт. Пользователь без аккаунтов (легаси) = полный доступ.
        user_ids = sorted({p.user_id for p in profiles if p.user_id is not None})
        if not user_ids:
            return []
        trial_map = await self._repository.get_users_with_trial(user_ids)
        accounts_map = await self._repository.accounts_by_users(user_ids)
        now = datetime.now(UTC)
        scoring_ids = {
            uid
            for uid in user_ids
            if effective_options(accounts_map.get(uid, []), trial_map.get(uid), now=now).has_option(
                "scoring"
            )
        }
        profiles = [p for p in profiles if p.user_id in scoring_ids]
        if not profiles:
            return []
        kw_map = await self._repository.list_profiles_keywords([p.id for p in profiles])
        return [
            ProfileRunContext(
                profile=p,
                keywords=kw_map.get(p.id, {}).get("keywords", []),
                exclusion_words=kw_map.get(p.id, {}).get("exclusion_words", []),
                target_regions=p.target_regions or [],
                max_region_distance_km=p.max_region_distance_km,
            )
            for p in profiles
        ]

    @staticmethod
    def _profile_has_valid_competencies(profile: Any) -> bool:
        """Профиль пригоден для сбора: компетенции — валидная непустая схема."""
        from zakupki_parser.storage.competencies import (
            CompetenciesError,
            is_empty,
            parse_competencies,
        )

        try:
            model = parse_competencies(profile.competencies or "")
        except CompetenciesError:
            return False
        return not is_empty(model)

    async def _recovery_allowed_profile_ids(self, profile_ids: list[int]) -> set[int]:
        """Профили, которым recovery может ставить fit-задания (по опциям владельца).

        Возвращает подмножество ``profile_ids``, чьи владельцы сейчас имеют
        эффективный доступ к опции ``scoring`` (триал либо активный аккаунт).
        Профиль без владельца (user_id IS NULL, легаси) пропускается как раньше.
        """
        if not profile_ids:
            return set()
        owner_map = await self._repository.profile_user_map(profile_ids)
        user_ids = sorted({uid for uid in owner_map.values() if uid is not None})
        trial_map = await self._repository.get_users_with_trial(user_ids) if user_ids else {}
        accounts_map = await self._repository.accounts_by_users(user_ids) if user_ids else {}
        now = datetime.now(UTC)
        allowed: set[int] = set()
        for profile_id, user_id in owner_map.items():
            if user_id is None or effective_options(
                accounts_map.get(user_id, []), trial_map.get(user_id), now=now
            ).has_option("scoring"):
                allowed.add(profile_id)
        return allowed

    async def run_service(self) -> None:
        """Бесконечный цикл: проход -> ожидание таймера."""
        await self.start()
        try:
            while not self._stop.is_set():
                self._iteration += 1
                await self.run_once(self._iteration)
                logger.info("Цикл завершён, ожидание %d с", self._cfg.ops.timeout_seconds)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._cfg.ops.timeout_seconds)
                except TimeoutError:
                    continue
        finally:
            await self.stop()

    async def _recover_scoring_queue(self, iteration: int = 0) -> None:
        """Догоняющая постановка пар (закупка, профиль) в очередь скоринга.

        Ищет в БД пары (закупка, профиль), у которых профиль отобрал закупку
        (``matched_keywords`` непуст), но для ЭТОГО профиля результат fit не записан
        (``fit_score IS NULL``), и она не поставлена в очередь (``scoring_queued_at
        IS NULL``) либо обновлялась после постановки / метка старше
        ``recovery_ttl_seconds`` (задание потеряно — воркер снял задачу, очередь
        очищена). Ставит задание fit с приоритетом по времени обновления/публикации.

        Идемпотентно: метка пишется только после успешного enqueue, поэтому
        повторно уже поставленные пары не дублируются. При первом же сбое enqueue
        (транспорт снова недоступен) recovery прекращается до следующего цикла.
        """
        if not self._cfg.score.scoring_transport_url or self._repository is None:
            return
        transport = ScoringTransportClient(
            self._cfg.score.scoring_transport_url,
            auth_token=self._cfg.ops.auth.internal_token,
        )
        now = datetime.now(UTC)
        ttl = self._cfg.score.recovery_ttl_seconds
        queued_before = now - timedelta(seconds=ttl) if ttl > 0 else None
        for _ in range(50):  # не более 50 партий по 200 за цикл
            items = await self._repository.find_unscored(limit=200, queued_before=queued_before)
            if not items:
                return
            # Recovery не должен тратить деньги владельцев, у которых опция скоринга
            # сейчас недоступна (триал истёк / опция отключена в аккаунте): те же
            # правила, что и для новых обходов (_gather_profile_ctxs).
            allowed_profiles = await self._recovery_allowed_profile_ids(
                [int(item["profile_id"]) for item in items]
            )
            for item in items:
                if item["profile_id"] not in allowed_profiles:
                    continue
                ts = item["update_date"] or item["publication_date"]
                priority = ts.timestamp() if ts is not None else now.timestamp()
                # Пер-профильная постановка (BR-07): задания ставятся/отмечаются для
                # каждого профиля, отобравшего закупку (matched_keywords непуст);
                # без profile_id задание ставиться не может — скоринг привязан к
                # компетенциям профиля.
                try:
                    await transport.enqueue(item["id"], priority, profile_id=item["profile_id"])
                    await self._repository.mark_scoring_queued(item["id"], item["profile_id"], now)
                    # Итерация recovery = текущая итерация цикла: фиксируем батч
                    # для журнала «Метрики» (закупка встала в очередь этого прохода).
                    if iteration:
                        await self._repository.mark_scoring_iteration(item["id"], iteration)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Recovery очереди скоринга прерван: %s (профиль %s) не поставлена (%s)",
                        item["id"],
                        item["profile_id"],
                        exc,
                    )
                    return
            logger.info(
                "Recovery очереди скоринга: поставлено пар (закупка, профиль): %d",
                len(items),
            )

    async def _parse_platform(
        self,
        platform_id: str,
        platform: PlatformDom,
        profiles: list[ProfileRunContext],
        iteration: int = 0,
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
                iteration=iteration,
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
