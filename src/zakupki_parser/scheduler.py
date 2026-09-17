"""Таймерный цикл запуска парсера по списку сайтов из ``config_service.yaml``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom
from zakupki_parser.logging_conf import reset_run_context, set_run_context, setup_logging
from zakupki_parser.notify import Notifier
from zakupki_parser.okpd import okpd_code_covered_by_prefixes
from zakupki_parser.parser.orchestrator import Orchestrator
from zakupki_parser.parser.orchestrator.context import ProfileRunContext
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import ALL_PLATFORMS_SENTINEL, Database, Profile
from zakupki_parser.storage.repository import ProcurementRepository
from zakupki_parser.storage.repository.accounts import effective_options

# id системного «индексного» профиля (IndexingConfig, _build_system_index_ctx):
# не персистится в БД (autoincrement выдаёт только положительные id), отрицательное
# значение гарантированно не совпадёт ни с одним реальным Profile.id.
SYSTEM_INDEX_PROFILE_ID = -1

logger = logging.getLogger(__name__)


@dataclass
class _CycleAccumulator:
    """Сводка одного прохода (``run_once``/``_run_refresh_pass``), devops-мониторинг.

    Заполняется ``_process_platform`` по мере обработки площадок (может идти
    параллельно, но инкременты — простые атомарные операции event loop'а,
    без гонок между ``await``). ``platforms_failed`` считает и обычные сбои
    (см. ``_process_platform``), и ``CircuitOpenError`` — сайт временно
    недоступен тоже сбой обращения к площадке с точки зрения мониторинга.
    """

    platforms_total: int = 0
    platforms_failed: int = 0
    received: int = 0
    saved: int = 0


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
        # Внеочередные обходы (fast-start): профили, запрошенные через
        # ``request_profile_refresh``, обрабатываются немедленно, параллельно
        # текущему регулярному проходу (см. _regular_loop/_refresh_loop) — не
        # дожидаясь ни его завершения, ни следующего регулярного цикла.
        self._refresh_ids: set[int] = set()
        # Правка профиля может требовать не только внеочередного обхода, но и
        # перестройки его результатов сбора (procurement_evaluations) и/или
        # пересчёта скора (изменились компетенции) — см. request_profile_refresh.
        self._refresh_rebuild: set[int] = set()
        self._refresh_rescore: set[int] = set()
        self._refresh_event = asyncio.Event()
        # Throttle (не debounce): момент ЗАВЕРШЕНИЯ последнего внеочередного
        # обхода каждого профиля (монотонное время). Следующий обход того же
        # профиля не раньше чем через profile_refresh_debounce_seconds ПОСЛЕ
        # этого момента — защита от долбления площадок повторными правками, не
        # искусственная задержка перед КАЖДЫМ обходом: профиль без записи здесь
        # (ещё не обходился ни разу) обходится немедленно, без ожидания вообще.
        self._refresh_last_run_at: dict[int, float] = {}
        # Внеочередные обходы теперь выполняются ПАРАЛЛЕЛЬНО с регулярным проходом
        # (см. _regular_loop/_refresh_loop), не дожидаясь его завершения — задачи
        # запускаются через asyncio.create_task (не await инлайн), поэтому их нужно
        # явно отслеживать и отменять при остановке (create_task создаёт НЕЗАВИСИМУЮ
        # задачу: отмена run_service её саму по себе не отменяет).
        self._refresh_tasks: set[asyncio.Task[None]] = set()
        # ОБЩИЕ (не пересоздаются на каждый проход) семафоры конкурентности площадок:
        # нужны, чтобы параллельно идущие проходы (регулярный + один или несколько
        # внеочередных) вместе не превышали config_parser.yaml ->
        # max_concurrent_platforms/max_concurrent_per_domain — один семафор на ВСЕ
        # одновременные проходы, а не отдельный на каждый (иначе конкурентность к
        # одной и той же площадке удваивалась бы на каждый параллельно идущий
        # проход). Лимит перечитывается при каждом получении семафора (см.
        # _get_platform_sem/_get_domain_sem) — так горячая правка config_parser.yaml
        # (без рестарта парсера, см. routes/config.py: state_setter=...) по-прежнему
        # применяется к новым запросам семафора, как и раньше (раньше семафоры
        # пересоздавались на каждый проход с актуальным значением).
        self._platform_sem: asyncio.Semaphore | None = None
        self._platform_sem_limit: int | None = None
        self._domain_sems: dict[str, tuple[int, asyncio.Semaphore]] = {}

    async def start(self) -> None:
        setup_logging(self._cfg.logging)
        await self._db.connect()
        # Активность площадок синхронизируем в БД (источник истины — platforms).
        enabled = {s.platform_id for s in self._cfg.service.sites if s.enabled}
        await self._repository.sync_platform_enabled(enabled)

    async def stop(self) -> None:
        self._stop.set()
        # Отменяем ещё не завершившиеся задачи внеочередных обходов (запущены
        # asyncio.create_task в _refresh_loop, не await инлайн — см. __init__)
        # и дожидаемся их отмены ДО закрытия пула БД, иначе отменённая задача
        # могла бы обратиться к уже закрытому соединению.
        tasks = list(self._refresh_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._db.dispose()

    def _get_platform_sem(self) -> asyncio.Semaphore:
        """Общий (на все параллельно идущие проходы) семафор числа площадок.

        Пересоздаётся, только если лимит изменился (горячая правка
        config_parser.yaml -> max_concurrent_platforms без рестарта парсера) —
        иначе один и тот же объект переиспользуется всеми проходами.
        """
        limit = self._cfg.parser.max_concurrent_platforms
        if self._platform_sem is None or self._platform_sem_limit != limit:
            self._platform_sem = asyncio.Semaphore(limit)
            self._platform_sem_limit = limit
        return self._platform_sem

    def _get_domain_sem(self, dkey: str) -> asyncio.Semaphore:
        """Общий (на все параллельно идущие проходы) семафор одного домена/бэкенда.

        Пересоздаётся, только если лимит изменился (горячая правка
        config_parser.yaml -> max_concurrent_per_domain). Общий semaphore между
        проходами критичен именно для домена — это и есть защита «44-ФЗ/223-ФЗ
        одного сайта не идут параллельно», её нельзя ослаблять, когда регулярный
        и внеочередной проходы совпали по площадке.
        """
        limit = self._cfg.parser.max_concurrent_per_domain
        cached = self._domain_sems.get(dkey)
        if cached is None or cached[0] != limit:
            sem = asyncio.Semaphore(limit)
            self._domain_sems[dkey] = (limit, sem)
            return sem
        return cached[1]

    def _refresh_remaining(self, profile_id: int) -> float:
        """Throttle: сколько ещё секунд ждать до обхода профиля.

        0 — можно обходить прямо сейчас (в т.ч. ПЕРВЫЙ обход профиля: записи в
        ``_refresh_last_run_at`` ещё нет, ждать нечего и не от чего). Иначе —
        остаток ``profile_refresh_debounce_seconds`` от момента ЗАВЕРШЕНИЯ
        предыдущего обхода этого профиля.
        """
        last = self._refresh_last_run_at.get(profile_id)
        if last is None:
            return 0.0
        debounce = max(self._cfg.ops.profile_refresh_debounce_seconds, 0.0)
        return max(0.0, debounce - (time.monotonic() - last))

    def request_profile_refresh(
        self,
        profile_id: int,
        *,
        rebuild: bool = False,
        rescore: bool = False,
    ) -> None:
        """Помечает профиль как требующий внеочередного обхода (fast-start).

        Вызывается после создания/изменения включённого профиля (API-роуты).
        Планировщик обработает профиль немедленно, НЕ дожидаясь ни следующего
        регулярного цикла (``timeout_seconds``), ни завершения уже идущего
        регулярного прохода — внеочередной и регулярный проходы выполняются
        параллельно (``_regular_loop``/``_refresh_loop``), суммарная нагрузка
        на площадки ограничена общими семафорами (``_get_platform_sem``/
        ``_get_domain_sem``). Пригодность профиля (включён, владелец активен и
        имеет поиск) ещё раз проверяется в момент запуска внеочередного обхода;
        опция ``scoring`` владельца при этом НЕ исключает профиль из обхода
        (мониторинг работает без скоринга).

        ``rebuild`` — после обхода перестроить per-profile результаты сбора
        (``procurement_evaluations``) по текущей области захвата профиля:
        закупки, вышедшие из неё, из результатов удаляются, вошедшие — обновляются.
        ``rescore`` — изменились компетенции: у совпавших результатов сбросить
        устаревший скор, чтобы recovery пересчитал его по новым компетенциям.

        Throttle, не debounce: ПЕРВЫЙ обход профиля начинается немедленно (нет
        предыдущего обхода — нечего защищать задержкой). Повторный обход того же
        профиля не раньше чем через ``profile_refresh_debounce_seconds`` после
        завершения предыдущего (``_refresh_remaining``/``_refresh_last_run_at``) —
        это и есть защита от долбления площадок частыми правками. При
        ``profile_refresh_debounce_seconds=0`` ограничения нет вовсе — обходить
        профиль повторно можно сразу после завершения предыдущего обхода.
        """
        self._refresh_ids.add(profile_id)
        if rebuild:
            self._refresh_rebuild.add(profile_id)
        if rescore:
            self._refresh_rescore.add(profile_id)
        self._refresh_event.set()
        remaining = self._refresh_remaining(profile_id)
        logger.info(
            "Запрошен внеочередной обход профиля %s (%s)",
            profile_id,
            "начнётся сразу" if remaining <= 0 else f"не ранее чем через {remaining:.0f} с",
        )

    def profile_refresh_status(self, profile_id: int) -> dict[str, Any]:
        """Текущее состояние запроса внеочередного обхода профиля (для API/UI).

        Возвращает:
        - ``pending`` — профиль в очереди на внеочередной обход;
        - ``remaining_seconds`` — throttle: сколько ещё ждать после завершения
          ПРЕДЫДУЩЕГО обхода этого профиля (0 — обход начнётся сразу, включая
          самый первый обход профиля; None — профиль не в очереди вовсе).
        """
        pending = profile_id in self._refresh_ids
        remaining = self._refresh_remaining(profile_id) if pending else None
        return {"pending": pending, "remaining_seconds": remaining}

    async def run_once(self, iteration: int = 0) -> None:
        """Один регулярный проход: все включённые площадки обрабатываются параллельно.

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

        Stage D (план «индекс как основной механизм discovery»), маршрутизация
        ПО КОДУ, не по профилю целиком (``_split_ctxs_for_index_routing``): код
        профиля, покрытый ``IndexingConfig.okpd2_prefixes``, обслуживается
        синхронизацией из БД (``_sync_profiles_via_index`` -> ``rebuild_profile_
        results``, без обращения к площадке); код вне покрытия — обычным живым
        обходом, но узким — только по непокрытым кодам ЭТОГО профиля
        (``ProfileRunContext.crawl_okpd_codes``), а не по всему профилю. Профиль
        без покрытых кодов вообще (или индексация выключена) обходится живьём
        как раньше, без сужения. Системный индексный профиль всегда обходится
        живьём целиком — он и наполняет ``procurements``/``procurement_search_
        index``, на которых основана синхронизация остальных.
        """
        await self._recover_scoring_queue(iteration)
        await self._recover_index_queue(iteration)
        ctxs = await self._gather_profile_ctxs()
        index_sync_ctxs, live_ctxs = self._split_ctxs_for_index_routing(ctxs)
        if index_sync_ctxs:
            await self._sync_profiles_via_index(index_sync_ctxs)
        started_at = datetime.now(UTC)
        cycle = await self._run_platform_pass(live_ctxs, iteration, full_window=False)
        await self._record_cycle_stats(iteration, "regular", started_at, cycle)

    def _split_ctxs_for_index_routing(
        self, ctxs: list[ProfileRunContext]
    ) -> tuple[list[ProfileRunContext], list[ProfileRunContext]]:
        """Делит профили ПО КОДУ ОКПД2 на «синхронизировать из индекса» / «живой обход».

        Возвращает ``(index_sync, live)``. Профиль может попасть в ОБЕ группы
        одновременно — если часть его кодов покрыта ``IndexingConfig.
        okpd2_prefixes``, а часть нет: в ``live`` тогда идёт КОПИЯ контекста
        (``dataclasses.replace``) с ``crawl_okpd_codes``, суженным до
        непокрытого остатка (см. ``ProfileRunContext.crawl_okpd_codes`` и
        ``Orchestrator._build_units``) — сам ``profile`` не меняется, живой
        обход просто просит у площадки меньше кодов, чем полный диапазон
        профиля. Ничего не покрыто (или у профиля вообще нет кодов, или
        индексация выключена) -> обычный живой обход без сужения, как до
        Stage D. Системный индексный профиль (``is_system_index``) — всегда
        только живой обход, целиком: он сам источник данных для индекса.
        """
        index_sync: list[ProfileRunContext] = []
        live: list[ProfileRunContext] = []
        indexing = self._cfg.service.indexing
        for ctx in ctxs:
            if ctx.is_system_index or not indexing.enabled:
                live.append(ctx)
                continue
            codes = list(ctx.profile.okpd_codes or [])
            if not codes:
                live.append(ctx)
                continue
            covered = [
                c for c in codes if okpd_code_covered_by_prefixes(c, indexing.okpd2_prefixes)
            ]
            uncovered = [c for c in codes if c not in covered]
            if covered:
                index_sync.append(ctx)
            if not covered:
                live.append(ctx)  # индекс не применим вовсе — обычный живой обход
            elif uncovered:
                live.append(replace(ctx, crawl_okpd_codes=uncovered))  # частичное покрытие
            # covered и не uncovered: полностью покрыт — в live не попадает вовсе.
        return index_sync, live

    async def _sync_profiles_via_index(self, ctxs: list[ProfileRunContext]) -> None:
        """Синхронизация профилей (или покрытой индексом части их кодов) из БД.

        Тот же ``rebuild_profile_results``, что и «горячий» пересбор при правке
        профиля (``_rebuild_profile_results``), но с ``rescore=False`` (это не
        реакция на изменение компетенций — обычная синхронизация; сброс скора
        при смене компетенций по-прежнему делает fast-start обход) и вызывается
        на каждом регулярном цикле, а не только по событию правки. Передаётся
        ПОЛНЫЙ ``ctx.profile`` (не суженный) — ``rebuild_profile_results`` сам
        сверяет каждую найденную в БД закупку с полным диапазоном профиля,
        сужение (``crawl_okpd_codes``) актуально только для живого обхода.
        Сбой одного профиля не должен останавливать синхронизацию остальных.
        """
        if self._repository is None:
            return
        for ctx in ctxs:
            try:
                stats = await self._repository.rebuild_profile_results(
                    ctx.profile,
                    ctx.keywords,
                    ctx.exclusion_words,
                    rescore=False,
                    use_document_index=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Синхронизация профиля %s через индекс не удалась: %s", ctx.profile.id, exc
                )
                continue
            logger.info(
                "Индекс: синхронизация профиля %s (покрытая индексом часть кодов): %s",
                ctx.profile.id,
                stats,
            )

    async def _record_cycle_stats(
        self,
        iteration: int,
        kind: str,
        started_at: datetime,
        cycle: _CycleAccumulator,
    ) -> None:
        """Пишет сводку прохода в ``parser_cycle_stats`` (devops-мониторинг).

        Best-effort: сбой записи (БД временно недоступна) не должен ронять
        планировщик — только предупреждение в лог, как и остальные devops-only
        побочные записи в этом модуле (напр. recovery очереди скоринга).
        Пустой проход (``platforms_total == 0`` — нет включённых профилей, dev-
        окружение без пользователей) не пишется: это не цикл обработки, а его
        отсутствие, и не должен разбавлять средние на вкладке «Мониторинг».
        """
        if self._repository is None or cycle.platforms_total == 0:
            return
        try:
            await self._repository.record_cycle_stats(
                iteration=iteration,
                kind=kind,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                platforms_total=cycle.platforms_total,
                platforms_failed=cycle.platforms_failed,
                received=cycle.received,
                saved=cycle.saved,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось сохранить сводку цикла (%s, итерация %d): %s", kind, iteration, exc
            )

    async def _run_platform_pass(
        self,
        ctxs: list[ProfileRunContext],
        iteration: int = 0,
        *,
        full_window: bool = False,
    ) -> _CycleAccumulator:
        """Обход включённых площадок для набора профилей (общая часть прохода).

        Используется и регулярным ``run_once`` (все профили), и внеочередным
        обходом ``_run_refresh_pass`` (только затронутые профили) — оба могут
        выполняться ОДНОВРЕМЕННО (см. ``_regular_loop``/``_refresh_loop``), в т.ч.
        несколько ``_run_platform_pass`` параллельно; семафоры конкурентности
        (``_get_platform_sem``/``_get_domain_sem``) — ОБЩИЕ на все параллельно
        идущие вызовы, поэтому суммарная нагрузка на площадки не превышает
        настроенных лимитов, даже когда два прохода совпали по площадке (тогда
        просто один из них дожидается доменного семафора, как и любые два
        обращения к площадке внутри одного прохода). При ``full_window=True``
        обход каждого профиля идёт по полному окну ``default_cutoff_days``
        (история для нового профиля), а не от инкремента ``last_processed_date``
        площадки.

        Возвращает сводку прохода (devops-мониторинг, ``parser_cycle_stats``) —
        вызывающий (``run_once``/``_run_refresh_pass``) дописывает временные метки
        и персистит.
        """
        cycle = _CycleAccumulator()
        if not ctxs or self._repository is None:
            return cycle
        enabled_platforms = await self._repository.enabled_platform_ids()

        async def _run_platform(platform_id: str, profiles: list[ProfileRunContext]) -> None:
            d_sem = self._get_domain_sem(self._domain_key(platform_id))
            # Единый порядок захвата (глобальный -> доменный) исключает deadlock.
            async with self._get_platform_sem(), d_sem:
                await self._process_platform(
                    platform_id, profiles, iteration, full_window=full_window, cycle=cycle
                )

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
        return cycle

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
        self,
        platform_id: str,
        profiles: list[ProfileRunContext],
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: _CycleAccumulator | None = None,
    ) -> None:
        """Обрабатывает одну площадку для набора профилей.

        ``cycle`` — накопитель сводки всего прохода (devops-мониторинг): успех
        добавляет received/saved площадки, любой сбой (включая CircuitOpenError —
        площадка временно недоступна) считается в ``platforms_failed``. Аргумент
        опционален только ради обратной совместимости существующих тестов,
        обращающихся к ``_process_platform`` напрямую без агрегации цикла.

        Дополнительно пишет ПЕР-ПЛОЩАДОЧНУЮ статистику (``_record_platform_stats``,
        ``parser_platform_stats``) — вкладка «Мониторинг» показывает не только
        сводку цикла целиком, но и разбивку по каждой площадке (рассчитано на
        рост числа площадок далеко за текущие 10, см. докстринг
        ``ParserPlatformStats``).
        """
        platform = self._cfg.dom.platforms.get(platform_id)
        if platform is None:
            logger.warning(
                "platform_id %s отсутствует в config_dom.yaml, пропуск",
                platform_id,
            )
            return
        logger.info(
            "Обработка площадки: %s (профилей: %d, итерация: %d%s)",
            platform_id,
            len(profiles),
            iteration,
            ", полное окно" if full_window else "",
        )
        # Контекст для логов: последующие записи этой площадки (и её подзадач)
        # автоматически получают префикс [platform#iteration] (см. logging_filter).
        token = set_run_context(platform_id, iteration)
        if cycle is not None:
            cycle.platforms_total += 1
        platform_started_at = datetime.now(UTC)
        try:
            stats = await self._parse_platform(
                platform_id, platform, profiles, iteration, full_window=full_window
            )
            if cycle is not None:
                cycle.received += stats.get("received", 0)
                cycle.saved += stats.get("saved", 0)
            await self._record_platform_stats(
                platform_id,
                iteration,
                platform_started_at,
                success=True,
                received=stats.get("received", 0),
                saved=stats.get("saved", 0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка обработки площадки %s: %s", platform_id, exc)
            if cycle is not None:
                cycle.platforms_failed += 1
            await self._record_platform_stats(
                platform_id,
                iteration,
                platform_started_at,
                success=False,
                received=0,
                saved=0,
                error_message=str(exc),
            )
        finally:
            reset_run_context(token)
        if self._on_update is not None:
            await self._on_update()

    async def _record_platform_stats(
        self,
        platform_id: str,
        iteration: int,
        started_at: datetime,
        *,
        success: bool,
        received: int,
        saved: int,
        error_message: str | None = None,
    ) -> None:
        """Пишет статистику одной площадки (``parser_platform_stats``, best-effort).

        Сбой записи не должен ронять обработку площадки — только предупреждение
        в лог, как и остальные devops-only побочные записи в этом модуле.
        """
        if self._repository is None:
            return
        try:
            await self._repository.upsert_platform_stats(
                platform_id=platform_id,
                iteration=iteration,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                success=success,
                received=received,
                saved=saved,
                error_message=error_message,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось сохранить статистику площадки %s: %s", platform_id, exc)

    def _profile_on_platform(self, ctx: ProfileRunContext, platform_id: str) -> bool:
        """True, если профиль относится к площадке.

        ``target_etp`` содержит либо ``ALL_PLATFORMS_SENTINEL`` («все площадки»,
        включая добавленные позже — см. ``storage/db/profile.py``), либо явный
        список id конкретных площадок. ПУСТОЙ список — ни одной площадки
        (2026-09, решение пользователя): раньше пустой список неявно означал
        «все», что в форме профиля выглядело как «площадки не выбраны» и
        вводило в заблуждение.
        """
        etp = set(ctx.profile.target_etp or [])
        return ALL_PLATFORMS_SENTINEL in etp or platform_id in etp

    async def _gather_profile_ctxs(
        self, only_ids: set[int] | None = None
    ) -> list[ProfileRunContext]:
        """Профили пользователей + системный «индексный» профиль (если включён).

        Системный профиль (``IndexingConfig``, без владельца-пользователя, не
        хранится в БД) добавляется только в РЕГУЛЯРНЫЙ обход (``only_ids is None``)
        — в целевой внеочередной обход конкретных изменённых профилей
        (``only_ids`` задан, ``_run_refresh_pass``) не попадает: он не привязан к
        какому-либо редактируемому пользователем профилю и не должен провоцировать
        собственный ``full_window=True`` пересбор по чужому триггеру.
        """
        ctxs = await self._gather_user_profile_ctxs(only_ids=only_ids)
        if only_ids is None:
            index_ctx = self._build_system_index_ctx()
            if index_ctx is not None:
                ctxs.append(index_ctx)
        return ctxs

    def _build_system_index_ctx(self) -> ProfileRunContext | None:
        """Синтетический «индексный» профиль (фоновая индексация по ОКПД2).

        Не привязан к пользователю: не хранится в БД, не виден в UI/API, участвует
        только в обходе площадок наравне с пользовательскими профилями. Пустые
        ``keywords``/``exclusion_words`` — по семантике R9 («пустой список — фильтра
        нет») сохраняются ВСЕ закупки указанного диапазона ОКПД2 (см.
        ``RecordProcessingMixin._process_list_record``), независимо от того, совпали
        ли они с чьими-либо ключевыми словами. ``scoring_allowed=False`` и пустые
        keywords гарантируют, что LLM-скоринг по этому контексту не запускается —
        только сохранение записи (для последующей индексации документов, см.
        ``indexing_service``).
        """
        indexing = self._cfg.service.indexing
        if not indexing.enabled or not indexing.okpd2_prefixes:
            return None
        excluded = set(indexing.excluded_platforms)
        target_etp = [pid for pid in self._cfg.dom.platforms if pid not in excluded]
        if not target_etp:
            return None
        profile = Profile(
            id=SYSTEM_INDEX_PROFILE_ID,
            user_id=None,
            name="__system_index__",
            enabled=True,
            is_active=False,
            target_etp=target_etp,
            target_laws=[],
            target_regions=[],
            okpd_codes=list(indexing.okpd2_prefixes),
            competencies="",
            questions=[],
        )
        return ProfileRunContext(
            profile=profile,
            keywords=[],
            exclusion_words=[],
            target_regions=[],
            scoring_allowed=False,
            search_in_documents=False,
            is_system_index=True,
        )

    async def _gather_user_profile_ctxs(
        self, only_ids: set[int] | None = None
    ) -> list[ProfileRunContext]:
        """Включённые профили незаблокированных пользователей + слова (BR-07).

        Пустой список — профилей нет: обходы не строятся (dev-режим).
        ``only_ids`` — подмножество профилей (внеочередной обход затронутых
        профилей); фильтр применяется после тех же правил пригодности, что и для
        регулярного прохода.

        Мониторинг (сбор закупок) отделён от платной LLM-опции ``scoring``: в обход
        попадает любой включённый профиль активного пользователя с доступным
        поиском (бесплатная опция ``search``), в т.ч. поисковый профиль без
        компетенций (BR-09). Доступность опции ``scoring`` больше НЕ исключает
        профиль из сбора — она лишь отражается флагом ``scoring_allowed`` контекста:
        профили владельцев без опции собираются, но задания на внешний LLM-скоринг
        по ним не ставятся (см. ``RecordProcessingMixin._process_list_record``).
        """
        if self._repository is None:
            return []
        profiles = await self._repository.list_enabled_profiles_for_active_users()
        if not profiles:
            return []
        if only_ids is not None:
            profiles = [p for p in profiles if p.id in only_ids]
        # Доступность опций считаем по пользователям профилей: триал либо активный
        # аккаунт. Пользователь без аккаунтов (легаси) = полный доступ.
        user_ids = sorted({p.user_id for p in profiles if p.user_id is not None})
        if not user_ids:
            return []
        trial_map = await self._repository.get_users_with_trial(user_ids)
        accounts_map = await self._repository.accounts_by_users(user_ids)
        now = datetime.now(UTC)
        eff = {
            uid: effective_options(accounts_map.get(uid, []), trial_map.get(uid), now=now)
            for uid in user_ids
        }
        # Мониторинг гейтим бесплатной опцией поиска («search»), а не «scoring»:
        # владельцу без платного скоринга сбор закупок всё равно доступен.
        monitor_ids = {uid for uid in user_ids if eff[uid].has_option("search")}
        profiles = [p for p in profiles if p.user_id in monitor_ids]
        if not profiles:
            return []
        scoring_ids = {uid for uid in user_ids if eff[uid].has_option("scoring")}
        kw_map = await self._repository.list_profiles_keywords([p.id for p in profiles])
        return [
            ProfileRunContext(
                profile=p,
                keywords=kw_map.get(p.id, {}).get("keywords", []),
                exclusion_words=kw_map.get(p.id, {}).get("exclusion_words", []),
                target_regions=p.target_regions or [],
                max_region_distance_km=p.max_region_distance_km,
                # LLM-скоринг допустим только при доступной опции «scoring» И валидных
                # непустых компетенциях профиля (без них внешний скоринг бессмыслен).
                scoring_allowed=(
                    p.user_id in scoring_ids and self._profile_has_valid_competencies(p)
                ),
                search_in_documents=bool(p.search_in_documents),
            )
            for p in profiles
        ]

    @staticmethod
    def _profile_has_valid_competencies(profile: Any) -> bool:
        """Профиль пригоден для LLM-скоринга: компетенции — валидная непустая схема."""
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

    def _next_refresh_wait(self) -> float | None:
        """Через сколько секунд ХОТЯ БЫ один накопленный профиль станет доступен
        для обхода (throttle от завершения его предыдущего обхода, см.
        ``_refresh_remaining``). ``None`` — очередь пуста, ждать нечего."""
        if not self._refresh_ids:
            return None
        return min(self._refresh_remaining(pid) for pid in self._refresh_ids)

    async def run_service(self) -> None:
        """Запускает регулярный и внеочередной циклы ПАРАЛЛЕЛЬНО (не await один
        за другим): внеочередной обход (fast-start) не ждёт завершения уже
        идущего регулярного прохода — раньше оба цикла жили в одном
        последовательном ``while``, и внеочередной обход, попавший на время
        работы длинного регулярного прохода (типично — самый первый после
        старта парсера, «холодный», без ``last_processed_date``), был вынужден
        ждать его завершения целиком, даже если throttle уже разрешал обход
        немедленно. Оба цикла используют ОБЩИЕ семафоры конкурентности
        площадок (``_get_platform_sem``/``_get_domain_sem``) — параллельность
        двух проходов не удваивает нагрузку на площадки.
        """
        await self.start()
        try:
            regular = asyncio.create_task(self._regular_loop())
            refresh = asyncio.create_task(self._refresh_loop())
            await asyncio.gather(regular, refresh)
        finally:
            await self.stop()

    async def _regular_loop(self) -> None:
        """Регулярные проходы через ``timeout_seconds`` (пауза отсчитывается от
        завершения предыдущего прохода — расписание не «плывёт» из-за внеочередных
        обходов, которые теперь идут в параллельном ``_refresh_loop``)."""
        while not self._stop.is_set():
            self._iteration += 1
            await self.run_once(self._iteration)
            logger.info("Цикл завершён, ожидание %d с", self._cfg.ops.timeout_seconds)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._cfg.ops.timeout_seconds)

    async def _refresh_loop(self) -> None:
        """Внеочередные обходы (fast-start, throttle) — независимый цикл, не
        дожидается ``_regular_loop``. Как только throttle отпускает какой-то
        накопленный профиль (``_next_refresh_wait``), для него запускается
        ОТДЕЛЬНАЯ параллельная задача (``asyncio.create_task``, не await
        инлайн) — так несколько профилей, ставших доступными почти
        одновременно, тоже обходятся параллельно, а не друг за другом.
        Задача отслеживается в ``_refresh_tasks`` (см. ``stop()``) и гасит
        собственные исключения (см. ``_run_refresh_pass_guarded``) — сбой
        обхода одного профиля не должен останавливать ни этот цикл, ни
        планировщик в целом.
        """
        while not self._stop.is_set():
            wait_for = self._next_refresh_wait()
            reason = await self._wait_signal_or_timeout(wait_for)
            if reason == "stop":
                break
            self._refresh_event.clear()
            if not self._refresh_ids:
                continue
            self._iteration += 1
            iteration = self._iteration
            task = asyncio.create_task(self._run_refresh_pass_guarded(iteration))
            self._refresh_tasks.add(task)
            task.add_done_callback(self._refresh_tasks.discard)

    async def _run_refresh_pass_guarded(self, iteration: int) -> None:
        """Обёртка ``_run_refresh_pass`` для запуска через ``asyncio.create_task``:
        задача детached (не await инлайн из ``_refresh_loop``), поэтому необработанное
        исключение молча потерялось бы (лишь предупреждение asyncio «Task exception
        was never retrieved») — логируем явно, тем же логгером, что и остальные
        сбои планировщика."""
        try:
            await self._run_refresh_pass(iteration)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Внеочередной обход (итерация %d) завершился ошибкой: %s", iteration, exc)

    async def _wait_signal_or_timeout(self, timeout: float | None) -> str:
        """Ждёт stop/refresh-сигнал до истечения ``timeout`` (``None`` — бессрочно).

        Возвращает ``"stop"``/``"refresh"``/``"timeout"``. Отмена внешней задачи
        (остановка парсера) отменяет внутренние задачи ожидания.
        """
        stop_task = asyncio.create_task(self._stop.wait())
        refresh_task = asyncio.create_task(self._refresh_event.wait())
        try:
            await asyncio.wait(
                (stop_task, refresh_task),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (stop_task, refresh_task):
                if not task.done():
                    task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(stop_task, refresh_task, return_exceptions=True)
        if self._stop.is_set():
            return "stop"
        if self._refresh_event.is_set():
            return "refresh"
        return "timeout"

    async def _run_refresh_pass(self, iteration: int = 0) -> None:
        """Внеочередной обход профилей, запрошенных через ``request_profile_refresh``.

        Пригодность профиля проверяется заново (``_gather_profile_ctxs`` с теми же
        правилами, что и регулярный проход: включён, владелец активен и имеет
        поиск). Обход идёт в режиме полного окна ``default_cutoff_days``:
        созданный/изменённый профиль должен увидеть историю (ретроспективное
        сопоставление слов по уже сохранённым закупкам), а не только инкремент от
        ``last_processed_date`` площадки.

        Throttle (``_refresh_remaining``), не кап «раз за цикл»: обрабатываются
        только профили, вышедшие из throttle прямо сейчас (обычно все накопленные —
        планировщик вызывает этот метод именно тогда, когда КАКОЙ-ТО профиль
        готов, см. ``run_service``/``_next_refresh_wait``); профиль, ещё не
        отошедший от предыдущего обхода, остаётся накопленным и будет обойдён,
        как только истечёт ``profile_refresh_debounce_seconds`` с завершения
        предыдущего его обхода — не дожидаясь границы регулярного цикла.

        Маршрутизация по коду (Stage D), как и в регулярном цикле
        (``_split_ctxs_for_index_routing``): коды профиля, покрытые индексом,
        синхронизируются из БД без обращения к площадке; живой full-window
        обход запрашивается только по непокрытым кодам (или по всем, если
        индекс профилю вообще не подходит). Раньше здесь ВСЕГДА следовал живой
        обход по всем кодам сразу после «горячего» пересбора — избыточно для
        уже полностью покрытых индексом профилей: он даёт даже более полную
        ретроспективу (не ограничен окном ``default_cutoff_days``), чем
        ограниченный по времени живой обход, так что для них повторный живой
        проход не нужен вовсе.
        """
        if not self._refresh_ids:
            return
        profile_ids = [pid for pid in self._refresh_ids if self._refresh_remaining(pid) <= 0]
        if not profile_ids:
            return
        # Метки перестройки/пересчёта снимаем со всех запрошенных (в т.ч. тех,
        # кто не пригоден): причина привязана к конкретной правке профиля.
        requested = set(profile_ids)
        rebuild_ids = self._refresh_rebuild & requested
        rescore_ids = self._refresh_rescore & requested
        self._refresh_ids.difference_update(profile_ids)
        self._refresh_rebuild.difference_update(requested)
        self._refresh_rescore.difference_update(requested)
        ctxs = await self._gather_profile_ctxs(only_ids=requested)
        if not ctxs:
            return
        # Перестройка результатов сбора по новой области захвата (и пересчёт скора,
        # если изменились компетенции) — до обхода площадок: обход дособерёт новые
        # закупки, а сброшенный скор recovery поставит на пересчёт в этом же цикле.
        for ctx in ctxs:
            if ctx.profile.id in rebuild_ids:
                # Пересчёт устаревшего скора только когда профиль действительно
                # пригоден для LLM-скоринга (опция владельца + валидные компетенции):
                # иначе сброс лишь потерял бы посчитанный результат без пересчёта.
                await self._rebuild_profile_results(
                    ctx,
                    rescore=ctx.profile.id in rescore_ids and ctx.scoring_allowed,
                )
        index_sync_ctxs, live_ctxs = self._split_ctxs_for_index_routing(ctxs)
        # Профили из rebuild_ids уже синхронизированы выше (_rebuild_profile_results
        # сам делает use_document_index=True) — не дублируем синхронизацию для них.
        remaining_sync = [c for c in index_sync_ctxs if c.profile.id not in rebuild_ids]
        if remaining_sync:
            await self._sync_profiles_via_index(remaining_sync)
        logger.info(
            "Внеочередной обход начинается: профилей %d (%s), итерация %d",
            len(ctxs),
            ", ".join(str(c.profile.id) for c in ctxs),
            iteration,
        )
        started_at = datetime.now(UTC)
        cycle = await self._run_platform_pass(live_ctxs, iteration, full_window=True)
        await self._record_cycle_stats(iteration, "refresh", started_at, cycle)
        # Сброшенный при перестройке скор пересчитываем сразу после обхода
        # (те же правила recovery: опция scoring владельца, TTL).
        await self._recover_scoring_queue(iteration)
        # Throttle: следующий обход ЭТИХ профилей не раньше чем через debounce от
        # ЭТОГО момента (завершения текущего обхода) — см. _refresh_remaining.
        now = time.monotonic()
        for ctx in ctxs:
            self._refresh_last_run_at[ctx.profile.id] = now
        logger.info(
            "Внеочередной обход завершён: профилей %d (итерация %d)",
            len(ctxs),
            iteration,
        )

    async def _rebuild_profile_results(
        self, ctx: ProfileRunContext, *, rescore: bool = False
    ) -> None:
        """Перестраивает результаты сбора одного профиля (после изменения профиля).

        Используется внеочередным обходом при ``rebuild=True``: сверяет
        ``procurement_evaluations`` профиля с текущей областью захвата (см.
        ``ProcurementRepository.rebuild_profile_results``). При ``rescore`` у
        устаревших по компетенциям результатов сбрасывается скор — recovery
        поставит повторный fit.

        Мгновенный путь «горячего» пересбора (Stage B плана «индекс как основной
        механизм discovery»): если включена фоновая индексация (``IndexingConfig``),
        закупки, не совпавшие по ``subject``, дополнительно проверяются по тексту
        документов (``procurement_search_index``) прямо в БД, без обращения к
        площадкам — независимо от того, какой диапазон ОКПД2 сейчас настроен для
        индексации (``rebuild_profile_results`` сам проверяет «эта закупка
        проиндексирована», а не «её код в текущем диапазоне», см. его докстринг).
        Для профиля без ``okpd_codes`` оптимизация не применяется — без серверного
        ограничения диапазона доп. проверка по тексту документов была бы
        неограниченно широкой (весь проиндексированный каталог).
        """
        if self._repository is None:
            return
        indexing = self._cfg.service.indexing
        use_index = indexing.enabled and bool(ctx.profile.okpd_codes)
        stats = await self._repository.rebuild_profile_results(
            ctx.profile,
            ctx.keywords,
            ctx.exclusion_words,
            rescore=rescore,
            use_document_index=use_index,
        )
        logger.info(
            "Перестройка результатов профиля %s: %s",
            ctx.profile.id,
            stats,
        )

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

    async def _recover_index_queue(self, iteration: int = 0) -> None:
        """Догоняющий повтор сбойных записей фоновой индексации (Dead Letter Queue, §Stage A).

        Тот же паттерн, что ``_recover_scoring_queue``, применённый к стадии
        ``index``: находит ``procurement_search_index.status='error'`` записи, не
        трогавшиеся дольше ``IndexingConfig.retry_ttl_seconds``, и ставит их в
        очередь индексации заново. Записи, исчерпавшие ``max_attempts``, уже
        переведены в ``status='dead_letter'`` в ``save_index_result`` и сюда не
        попадают — для них retry прекращён, требуется ручное вмешательство
        (аналитик/devops, ``reset_index_entry_for_retry``).
        """
        indexing = self._cfg.service.indexing
        if (
            not indexing.enabled
            or not self._cfg.score.scoring_transport_url
            or self._repository is None
        ):
            return
        transport = ScoringTransportClient(
            self._cfg.score.scoring_transport_url,
            auth_token=self._cfg.ops.auth.internal_token,
        )
        now = datetime.now(UTC)
        updated_before = now - timedelta(seconds=indexing.retry_ttl_seconds)
        for _ in range(10):  # не более 10 партий по 200 за цикл
            items = await self._repository.retryable_index_errors(
                limit=200, updated_before=updated_before
            )
            if not items:
                return
            for item in items:
                ts = item["update_date"] or item["publication_date"]
                priority = ts.timestamp() if ts is not None else now.timestamp()
                try:
                    await transport.enqueue(
                        item["procurement_id"], priority, stage="index", profile_id=0
                    )
                    await self._repository.mark_index_retry_queued(item["procurement_id"], now)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Recovery очереди индексации прерван: закупка %s не поставлена (%s)",
                        item["procurement_id"],
                        exc,
                    )
                    return
            logger.info("Recovery очереди индексации: повторно поставлено закупок: %d", len(items))

    async def _parse_platform(
        self,
        platform_id: str,
        platform: PlatformDom,
        profiles: list[ProfileRunContext],
        iteration: int = 0,
        *,
        full_window: bool = False,
    ) -> dict[str, int]:
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
                return await orchestrator.run(page, profiles=profiles, full_window=full_window)
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
