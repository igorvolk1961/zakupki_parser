"""Оркестратор основного алгоритма парсинга одной площадки.

См. specification.md для детального описания шагов. Вспомогательные миксины:
``activity`` (активность), ``stop`` (условия прекращения), ``persistence`` (запись в БД),
``processing`` (обработка одной записи списка), ``crawl`` (обход страниц/API).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom, SearchCriteria
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.orchestrator.activity import ActivityMixin
from zakupki_parser.parser.orchestrator.crawl import CrawlMixin
from zakupki_parser.parser.orchestrator.persistence import PersistenceMixin
from zakupki_parser.parser.orchestrator.processing import RecordProcessingMixin
from zakupki_parser.parser.orchestrator.stop import StopMixin
from zakupki_parser.parser.organization import resolve_inn
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import Profile
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
        # Колбэк при сохранении закупки (живые обновления в web-демо).
        self._on_record_saved = on_record_saved
        # Авто-пуш задания на внешний скоринг в транспорт (ADR-7). Если адрес не задан —
        # внешний скоринг не запускается, закупка остаётся с дефолтным score.
        self._transport = (
            ScoringTransportClient(cfg.score.scoring_transport_url)
            if cfg.score.scoring_transport_url
            else None
        )
        # Кеш ИНН по ссылке на организацию: страницу организации грузим не чаще раза за проход.
        self._inn_cache: dict[str, str | None] = {}
        # Номера уже сохранённых закупок площадки — оптимизация повторного прохода
        # (relevance-режим): детальные страницы известных закупок не открываем.
        self._known_numbers: set[str] | None = None
        # Активный профиль (контекст клиентской фильтрации) и его слова
        # (таблица keywords, канонический источник, R9). Задаются в ``run``.
        self._client_profile: Profile | None = None
        self._client_keywords: list[str] = []
        self._client_exclusion_words: list[str] = []
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
        if self._is_known(number):
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

    async def run(self, page: Page) -> None:
        """Запускает проход по площадке на заданной ``page``."""
        if not self._site_cb.allow_request():
            raise CircuitOpenError("Сайт недоступен (circuit open)")

        # Глобальный режим sort_by_date_only: все площадки сортируются по дате.
        # Иначе — релевантность задаётся индивидуально (sort.by_relevance площадки).
        by_relevance = (not self._cfg.service.sort_by_date_only) and bool(
            self._platform.sort and self._platform.sort.by_relevance
        )
        if by_relevance:
            # Сортировка по релевантности: по дате НЕ отсекаем, обходим до конца пагинации.
            cutoff = None
            logger.info(
                "Площадка %s: сортировка по релевантности — фильтрация по дате отключена",
                self._platform_id,
            )
        elif self._repository is None:
            # БД недоступна (repository=None) — порог взять неоткуда, кроме default_cutoff_days.
            cutoff = self._now - timedelta(days=self._cfg.service.default_cutoff_days)
        else:
            # Поле даты стоп-порога: update_date, если площадка его поддерживает
            # (переменная update_date в карточке списка), иначе publication_date.
            date_field = (
                "update_date"
                if any(v.name == "update_date" for v in self._platform.list_config.variables)
                else "publication_date"
            )
            cutoff = await self._repository.last_processed_date(
                self._platform_id,
                self._now,
                self._cfg.service.default_cutoff_days,
                field=date_field,
            )
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
        search = self._platform.search
        # Активный профиль сервис-аккаунта — контекст КЛИЕНТСКОЙ пост-фильтрации (R9):
        # ключевые слова НЕ передаются на площадку; серверная фильтрация — только по
        # кодам ОКПД2 (+ обход «без кода»). Слова читаются из таблицы keywords
        # (канонический источник, ER: PROFILE -> KEYWORD).
        if self._repository is not None:
            user = await self._repository.first_user()
            profile = (
                await self._repository.get_active_profile(user.id) if user is not None else None
            )
            if profile is not None:
                kw = await self._repository.get_profile_keywords(profile.id)
                self._client_keywords = kw["keywords"]
                self._client_exclusion_words = kw["exclusion_words"]
            else:
                self._client_keywords = []
                self._client_exclusion_words = []
        else:
            profile = None
            self._client_keywords = []
            self._client_exclusion_words = []
        self._client_profile = profile

        # R9: ключевые слова не участвуют в серверном запросе — обходы строятся
        # только по кодам ОКПД2 (+ обход «без кода», только при
        # search_criteria.no_code_search). Критерии поиска берутся из активного
        # профиля (профиль → колонки okpd_codes/nmck_min/nmck_max); выбор по
        # состоянию (active_only) — только из глобального config_service.yaml
        # (search_criteria.active_only). Без профиля (dev/тесты) — fallback на
        # глобальный config_service.yaml.
        if self._client_profile is not None:
            base = SearchCriteria(
                okpd_codes=self._client_profile.okpd_codes or [],
                nmck_min=self._client_profile.nmck_min,
                nmck_max=self._client_profile.nmck_max,
                active_only=self._cfg.service.search_criteria.active_only,
            )
        else:
            base = self._cfg.service.search_criteria.model_copy()
        # Обход по кодам ОКПД2 имеет смысл, только если площадка реально фильтрует
        # по кодам (есть маппинг okpd2): иначе коды-only обход вернул бы весь список
        # (например roseltorg, где okpd2 не подключён).
        okpd_mapped = bool(search and "okpd2" in (search.criteria_map or {}))
        # Обход «без кода» (R9): клиентская фильтрация словами профиля. Выполняется
        # ОТДЕЛЬНЫМ проходом по всему реестру площадки (фильтр okpdPaths не ставится —
        # пустой список кодов площадка воспринимает как «любой код»), чтобы не терять
        # закупки, подходящие по словам, но вне заданных кодов ОКПД2. По умолчанию
        # ВЫКЛЮЧЕН: запускается только при глобальном флаге config_service.yaml
        # search_criteria.no_code_search (и наличии позитивных ключевых слов + search).
        has_positive_keywords = bool(self._client_keywords)
        no_code_walk = (
            has_positive_keywords
            and search is not None
            and self._cfg.service.search_criteria.no_code_search
        )
        if has_positive_keywords and not no_code_walk:
            logger.info(
                "Площадка %s: позитивные слова есть, но обход «без кода» выключен "
                "(search_criteria.no_code_search) — пропущен",
                self._platform_id,
            )

        crawled = False
        if base.okpd_codes and okpd_mapped:
            await self._crawl(page, cutoff, base, by_relevance, retry_cfg)
            crawled = True
        if no_code_walk:
            no_code = base.model_copy(update={"okpd_codes": []})
            logger.info(
                "Площадка %s: обход «без кода» (клиентская фильтрация словами профиля)",
                self._platform_id,
            )
            await self._crawl(page, cutoff, no_code, by_relevance, retry_cfg)
            crawled = True
        if not crawled:
            logger.warning(
                "Площадка %s: нет серверного фильтра (коды ОКПД2 не заданы/не подключены) "
                "и обход «без кода» недоступен — проход пропущен",
                self._platform_id,
            )

        logger.info(
            "Площадка %s: итог прохода — получено закупок: %d, сохранено: %d, уже было в БД: %d",
            self._platform_id,
            self._platform_stats["received"],
            self._platform_stats["saved"],
            self._platform_stats["known"],
        )
        self._site_cb.record_success()

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
