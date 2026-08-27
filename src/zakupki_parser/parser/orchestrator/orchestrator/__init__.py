"""Оркестратор основного алгоритма парсинга одной площадки.

См. specification.md для детального описания шагов. Вспомогательные миксины:
``activity`` (активность), ``stop`` (условия прекращения), ``persistence`` (запись в БД),
``processing`` (обработка одной записи списка), ``crawl`` (обход страниц/API).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom, SearchCriteria
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.orchestrator.activity import ActivityMixin
from zakupki_parser.parser.orchestrator.context import CrawlUnit, ProfileRunContext
from zakupki_parser.parser.orchestrator.crawl import CrawlMixin
from zakupki_parser.parser.orchestrator.persistence import PersistenceMixin
from zakupki_parser.parser.orchestrator.processing import RecordProcessingMixin
from zakupki_parser.parser.orchestrator.stop import StopMixin
from zakupki_parser.parser.organization import resolve_inn
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class Orchestrator(
    ActivityMixin,
    PersistenceMixin,
    StopMixin,
    RecordProcessingMixin,
    CrawlMixin,
):
    """Выполняет полный проход по закупкам площадки."""

    def __init__(
        self,
        cfg: AppConfig,
        platform_id: str,
        platform: PlatformDom,
        delayer: Delayer,
        repository: ProcurementRepository | None,
        notifier: Notifier,
        site_cb: CircuitBreaker,
        db_cb: CircuitBreaker,
        new_page: Callable[[], Awaitable[Page]] | None = None,
        now: datetime | None = None,
        on_record_saved: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._cfg = cfg
        self._platform_id = platform_id
        self._platform = platform
        self._delayer = delayer
        self._repository = repository
        self._notifier = notifier
        self._site_cb = site_cb
        self._db_cb = db_cb
        self._new_page = new_page
        self._now = now or datetime.now(UTC)
        # Колбэк при сохранении закупки (живые обновления в web-интерфейсе).
        self._on_record_saved = on_record_saved
        # Авто-пуш задания на внешний скоринг в транспорт (ADR-7). Если адрес не задан —
        # внешний скоринг не запускается, закупка остаётся с дефолтным score.
        self._transport = (
            ScoringTransportClient(
                cfg.score.scoring_transport_url, auth_token=cfg.ops.auth.internal_token
            )
            if cfg.score.scoring_transport_url
            else None
        )
        # Кеш ИНН по ссылке на организацию: страницу организации грузим не чаще раза за проход.
        self._inn_cache: dict[str, str | None] = {}
        # Номера уже сохранённых закупок площадки — оптимизация повторного прохода
        # (relevance-режим): детальные страницы известных закупок не открываем.
        self._known_numbers: set[str] | None = None
        # Профили текущего поискового обхода (мультипрофильная ветка веерной
        # фильтрации, BR-07): задаются в ``run`` перед каждым ``_crawl``.
        self._profile_ctxs: list[ProfileRunContext] = []
        # Досборка деталей площадки (BR-08): отдельным best-effort проходом ПОСЛЕ
        # получения результата скоринга. Контекст досборки (detail_api) хранится в БД,
        # проход идёт по find_scored_without_details — только по закупкам с fit_score.
        self._current_unit: CrawlUnit | None = None
        self._multi_run = False
        self._by_relevance = False
        # Агрегированная статистика прохода площадки (получено/сохранено/известно).
        self._platform_stats: dict[str, int] = {"received": 0, "saved": 0, "known": 0}

    async def _resolve_customer_inn(self, page: Page, customer_link: str | None) -> str | None:
        """ИНН заказчика с кешированием по ссылке на организацию.

        Сбой получения не прерывает обработку: возвращается None (ИНН nullable).
        """
        if not customer_link:
            return None
        if customer_link in self._inn_cache:
            return self._inn_cache[customer_link]
        inn = await resolve_inn(page, self._platform, customer_link)
        self._inn_cache[customer_link] = inn
        return inn

    def _is_known(self, number: Any) -> bool:
        """True, если закупка с номером уже сохранена в БД (повторный проход)."""
        return (
            self._known_numbers is not None
            and number is not None
            and str(number) in self._known_numbers
        )

    async def _process_container(
        self,
        page: Page,
        container: Locator,
    ) -> tuple[bool, Any, bool]:
        """Обрабатывает один контейнер записи о закупке (DOM-листер).

        Возвращает (известна ли запись как уже сохранённая в БД, номер закупки,
        сохранена ли запись в БД на этом шаге).
        """
        # 1) list-vars
        list_vars = await extract_from_scope(container, self._platform.list_config.variables)
        number = list_vars.get("number")

        # Оптимизация повторного прохода: закупка уже в БД — детальную страницу
        # не открываем (upsert не обновляет известные записи, поведение не меняется).
        # Для мультипрофильного прохода пропуск невозможен: запись нужна каждому
        # профилю (у другого профиля может ещё не быть оценки).
        if self._is_known(number) and not self._multi_run:
            logger.info("Закупка %s уже в БД — пропуск", number)
            return True, number, False

        # 2) ссылка на детальную страницу
        detail_link_loc = container.locator(self._platform.list_config.detail_link)
        if await detail_link_loc.count() == 0:
            logger.debug("Нет ссылки на детали, пропуск (number=%s)", number)
            return False, number, False
        detail_url = await detail_link_loc.first.get_attribute("href")
        if not detail_url:
            return False, number, False

        # Номер не извлёкся из карточки (селектор/паттерн не совпали) — достаём его из
        # URL детальной страницы (например, /procedure/COM14082600147/1 на roseltorg).
        # Иначе запись будет отброшена в repository.upsert как «нет number».
        url_re = self._platform.list_config.number_from_url_regex
        if not number and url_re:
            m = re.search(url_re, detail_url)
            if m:
                number = m.group(1) if m.lastindex else m.group(0)
                list_vars["number"] = number
                logger.info("Номер закупки извлечён из URL деталей: %s", number)
            else:
                logger.warning(
                    "Номер не извлечён ни из карточки, ни из URL %s (regex %s) — "
                    "запись будет пропущена",
                    detail_url,
                    url_re,
                )

        return await self._process_list_record(page, list_vars, detail_url, number)

    async def run(
        self,
        page: Page,
        *,
        profiles: Sequence[ProfileRunContext] | None = None,
    ) -> None:
        """Запускает проход по площадке на заданной ``page``.

        ``profiles`` — включённые профили незаблокированных пользователей,
        обрабатываемые на этой площадке. Если не заданы (``None``) — сохраняется
        прежнее поведение: активный профиль первого пользователя (dev/тесты).
        Идентичные запросы (идентичные критерии) разных профилей к одной площадке
        объединяются в один обход с веерной фильтрацией (BR-07).
        """
        if not self._site_cb.allow_request():
            raise CircuitOpenError("Сайт недоступен (circuit open)")

        # Глобальный режим sort_by_date_only: все площадки сортируются по дате.
        # Иначе — релевантность задаётся индивидуально (sort.by_relevance площадки).
        by_relevance = (not self._cfg.service.sort_by_date_only) and bool(
            self._platform.sort and self._platform.sort.by_relevance
        )
        self._by_relevance = by_relevance

        # Набор профилей-потребителей для этой площадки.
        explicit = profiles is not None
        if profiles is not None:
            run_profiles = [p for p in profiles if self._platform_selects(p)]
        else:
            run_profiles = await self._load_legacy_profiles()
        self._multi_run = len(run_profiles) > 1

        cutoff = await self._compute_cutoff(by_relevance, explicit, run_profiles)
        logger.info("Начало обработки площадки %s, порог даты: %s", self._platform_id, cutoff)

        # Оптимизация повторного прохода: грузим номера сохранённых закупок, чтобы
        # не открывать детальные страницы известных записей (upsert их не обновляет).
        if self._repository is not None:
            self._known_numbers = await self._repository.known_numbers(self._platform_id)
            logger.info(
                "Площадка %s: известно закупок в БД: %d",
                self._platform_id,
                len(self._known_numbers),
            )

        retry_cfg = self._cfg.parser.retry
        # R9: ключевые слова не участвуют в серверном запросе — обходы строятся
        # только по кодам ОКПД2 (+ обход «без кода»). Критерии берутся из профилей;
        # выбор по состоянию (active_only) — из глобального config_service.yaml.
        # Идентичные критерии разных профилей объединяются (дедупликация запросов).
        units = self._build_units(run_profiles)

        for unit in units:
            self._current_unit = unit
            # Профили текущего обхода (веерная фильтрация, R9). Для мультипрофильного
            # обхода ранний клиентский фильтр и пропуск «уже в БД» отключаются, чтобы
            # каждый профиль получил запись и сформировал собственную оценку.
            self._profile_ctxs = unit.profiles
            scope = "коды ОКПД2: %s" % (unit.criteria.okpd_codes or "весь список")
            logger.info(
                "Площадка %s: обход %s (%s), профилей в обходе: %d",
                self._platform_id,
                "«без кода»" if unit.kind == "no_code" else "по кодам",
                scope,
                len(unit.profiles),
            )
            await self._crawl(page, cutoff, unit.criteria, by_relevance, retry_cfg)
            self._profile_ctxs = []

        if not units:
            logger.warning(
                "Площадка %s: нет серверного фильтра (коды ОКПД2 не заданы/не подключены) "
                "и обход «без кода» недоступен — проход пропущен",
                self._platform_id,
            )

        # Досборка деталей площадки ПОСЛЕ получения результата скоринга (BR-08):
        # best-effort — только закупки с fit_score в БД (внешний сервис вернул
        # результат через POST /score); сбой деталей (напр. mos.ru 402) не роняет
        # проход и не влияет на уже выполненный скоринг.
        await self._collect_pending_details(page)

        logger.info(
            "Площадка %s: итог прохода — получено закупок: %d, сохранено: %d, уже было в БД: %d",
            self._platform_id,
            self._platform_stats["received"],
            self._platform_stats["saved"],
            self._platform_stats["known"],
        )
        self._site_cb.record_success()

    def _platform_selects(self, ctx: ProfileRunContext) -> bool:
        """True, если профиль относится к этой площадке.

        Ограничение по ``target_etp`` (зарезервировано, сейчас обычно пусто):
        пустой список — профиль участвует на всех площадках.
        """
        etp = set(ctx.profile.target_etp or [])
        return not etp or self._platform_id in etp

    async def _load_legacy_profiles(self) -> list[ProfileRunContext]:
        """Разрешение обхода без явно переданных профилей (dev/тесты).

        Возвращает пустой список: строится единый обход по глобальным критериям
        config_service.yaml (как ``profile=None``). Рабочий обход всегда получает
        профили от ``Scheduler._gather_profile_ctxs`` (мультитенантный источник),
        а не от «первого пользователя».
        """
        return []

    async def _compute_cutoff(
        self,
        by_relevance: bool,
        explicit: bool,
        run_profiles: list[ProfileRunContext],
    ) -> datetime | None:
        """Стоп-порог по дате для прохода площадки.

        В мультипрофильной ветке (несколько профилей, явный список) используем полное
        окно ``now - default_cutoff_days`` — иначе новый профиль потерял бы историю
        (``last_processed_date`` — инкремент от последней записи площадки).
        """
        if by_relevance:
            logger.info(
                "Площадка %s: сортировка по релевантности — фильтрация по дате отключена",
                self._platform_id,
            )
            return None
        if self._repository is None or (explicit and len(run_profiles) > 1):
            return self._now - timedelta(days=self._cfg.service.default_cutoff_days)
        # Поле даты стоп-порога: update_date, если площадка его поддерживает
        # (переменная update_date в карточке списка), иначе publication_date.
        date_field = (
            "update_date"
            if any(v.name == "update_date" for v in self._platform.list_config.variables)
            else "publication_date"
        )
        return await self._repository.last_processed_date(
            self._platform_id,
            self._now,
            self._cfg.service.default_cutoff_days,
            field=date_field,
        )

    @staticmethod
    def _criteria_units_key(criteria: SearchCriteria, kind: str) -> tuple[Any, ...]:
        """Ключ дедупликации обхода: одинаковые запросы к площадке объединяются."""
        return (
            kind,
            tuple(sorted(criteria.okpd_codes)),
            criteria.nmck_min,
            criteria.nmck_max,
            criteria.active_only,
        )

    def _build_units(self, run_profiles: list[ProfileRunContext]) -> list[CrawlUnit]:
        """Строит поисковые обходы (``CrawlUnit``) с дедупликацией запросов.

        Для каждого профиля: обход по кодам ОКПД2 (если коды заданы и площадка
        фильтрует по ним) и/или обход «без кода» (R9). Профили с одинаковыми
        критериями объединяются в один обход — запрос к площадке выполняется один раз.
        """
        search = self._platform.search
        sc = self._cfg.service.search_criteria
        okpd_mapped = bool(search and "okpd2" in (search.criteria_map or {}))

        if not run_profiles:
            # Без профилей (dev/тесты) — единый обход по глобальным критериям конфига.
            base = sc.model_copy()
            if base.okpd_codes and okpd_mapped:
                return [CrawlUnit(criteria=base, kind="codes", profiles=[])]
            return []

        dedup = self._cfg.service.deduplicate_requests
        units: dict[tuple[Any, ...], CrawlUnit] = {}
        unique = 0

        def _emit(
            key: tuple[Any, ...],
            criteria: SearchCriteria,
            kind: Literal["codes", "no_code"],
            ctx: ProfileRunContext,
        ) -> None:
            nonlocal unique
            if dedup:
                units.setdefault(key, CrawlUnit(criteria=criteria, kind=kind)).profiles.append(ctx)
            else:
                unique += 1
                units[(key, unique)] = CrawlUnit(criteria=criteria, kind=kind, profiles=[ctx])

        for ctx in run_profiles:
            base = SearchCriteria(
                okpd_codes=ctx.profile.okpd_codes or [],
                nmck_min=ctx.profile.nmck_min,
                nmck_max=ctx.profile.nmck_max,
                active_only=sc.active_only,
            )
            if base.okpd_codes and okpd_mapped:
                _emit(self._criteria_units_key(base, "codes"), base, "codes", ctx)
            # Обход «без кода» (R9): отдельный проход по всему реестру площадки.
            has_positive = bool(ctx.keywords)
            no_code_ok = has_positive and search is not None and sc.no_code_search
            if has_positive and not no_code_ok:
                logger.info(
                    "Площадка %s: обход «без кода» пропущен (no_code_search=false)",
                    self._platform_id,
                )
            if no_code_ok:
                no_code = base.model_copy(update={"okpd_codes": []})
                _emit(self._criteria_units_key(no_code, "no_code"), no_code, "no_code", ctx)
        return list(units.values())

    def _log_crawl_summary(
        self,
        criteria: SearchCriteria,
        received: int,
        saved: int,
        known: int,
    ) -> None:
        """Итоговая сводка одного поискового обхода (сколько получено с платформы).

        Обновляет агрегированную статистику площадки ``_platform_stats`` (для
        сводки всего прохода в ``run``).
        """
        scope = f"коды ОКПД2: {criteria.okpd_codes}" if criteria.okpd_codes else "весь список"
        skipped = max(0, received - saved - known)
        logger.info(
            "Площадка %s: обход (%s) — получено закупок: %d, сохранено: %d, "
            "уже в БД: %d, отсеяно/пропущено: %d",
            self._platform_id,
            scope,
            received,
            saved,
            known,
            skipped,
        )
        self._platform_stats["received"] += received
        self._platform_stats["saved"] += saved
        self._platform_stats["known"] += known
