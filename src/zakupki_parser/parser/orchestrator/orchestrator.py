"""Оркестратор основного алгоритма парсинга одной площадки.

См. specification.md для детального описания шагов. Вспомогательные миксины:
``activity`` (активность), ``stop`` (условия прекращения), ``persistence`` (запись в БД).
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from playwright.async_api import Locator, Page

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom, RetryConfig, SearchCriteria
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.cutoff import is_older_than_cutoff
from zakupki_parser.parser.detail import (
    detail_files,
    extract_detail_vars,
    files_page_url,
    open_detail,
)
from zakupki_parser.parser.detail_api import fetch_api_details
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.filtering import exclusions_present, keywords_match, matched_keywords
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.parser.lister import (
    _increment_url_page,
    extract_total_results,
    goto_next_page,
    iter_container_records,
    next_page_exists,
    open_list_page,
    setup_sort_and_filters,
)
from zakupki_parser.parser.lister.api import build_api_list_url, fetch_api_items, parse_api_item
from zakupki_parser.parser.orchestrator.activity import ActivityMixin
from zakupki_parser.parser.orchestrator.persistence import PersistenceMixin
from zakupki_parser.parser.orchestrator.stop import StopMixin
from zakupki_parser.parser.organization import capture_customer_link, resolve_inn
from zakupki_parser.retry import run_with_retry
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import Profile
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class Orchestrator(ActivityMixin, PersistenceMixin, StopMixin):
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

    async def _process_list_record(
        self,
        page: Page,
        list_vars: dict[str, Any],
        detail_url: str | None,
        number: Any,
        api_fields: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, bool]:
        """Общая обработка записи из списка (DOM или API): детали, stop, скоринг, запись.

        ``list_vars`` — переменные карточки списка (list_config.variables), ``detail_url`` —
        ссылка на детальную страницу, ``number`` — номер закупки, ``api_fields`` —
        доп. поля для извлечения деталей через API (``detail.api_format``). Возвращает
        (известна ли запись как уже сохранённая в БД, номер закупки, сохранена ли
        запись в БД на этом шаге).
        """
        if not detail_url:
            logger.debug("Нет ссылки на детали, пропуск (number=%s)", number)
            return False, number, False

        # Ранняя клиентская фильтрация (R9): subject уже есть в карточке списка —
        # применяем ключевые слова ДО запроса деталей, чтобы не тратить лимиты API
        # площадки на заведомо неподходящие закупки (например mos.ru HTTP 402).
        # Если subject в списке пуст — детали открываем, фильтр применится после.
        early_subject = str(list_vars.get("subject") or "")
        if early_subject and self._client_profile is not None:
            if not keywords_match(list_vars, self._client_keywords):
                logger.info(
                    "Закупка %s отброшена: нет совпадений с ключевыми словами профиля",
                    number,
                )
                return False, number, False
            if exclusions_present(list_vars, self._client_exclusion_words):
                logger.info(
                    "Закупка %s отброшена: слова-исключения в описании",
                    number,
                )
                return False, number, False

        # stop-условия по данным из деталей проверяются после извлечения деталей.
        # 3) детали: либо через открытый API площадки (детальная страница не
        #    открывается), либо переход на детальную страницу в отдельной вкладке,
        #    чтобы не терять страницу списка (итерация по контейнерам и пагинация
        #    продолжаются). «Возврат к списку» (п.10 ТЗ) — закрытие этой вкладки.
        files: list[dict[str, str]] = []
        customer_link: str | None = None
        api_inn: str | None = None
        detail_page: Page | None = None
        close_detail = False
        try:
            retry_cfg = self._cfg.parser.retry
            if self._platform.detail.api_format:
                detail_vars, files, api_inn = await run_with_retry(
                    partial(fetch_api_details, page, self._platform, list_vars, api_fields),
                    retry=retry_cfg,
                    circuit=self._site_cb,
                    label=f"Детали {number}",
                )
            else:
                if self._new_page is not None:
                    detail_page = await self._new_page()
                    close_detail = True
                else:
                    detail_page = page
                await run_with_retry(
                    lambda: open_detail(detail_page, detail_url, self._platform),
                    retry=retry_cfg,
                    circuit=self._site_cb,
                    label=f"Детали {number}",
                )
                detail_vars = await extract_detail_vars(detail_page, self._platform)
                customer_link = await capture_customer_link(detail_page, self._platform)
                # Доп. страницы деталей (например, ОКПД2 223-ФЗ на lot-list): переход
                # по ссылке с детальной страницы и извлечение дополнительных переменных.
                for spec in self._platform.detail.additional_pages:
                    try:
                        link = detail_page.locator(spec.link_selector).first
                        if await link.count() == 0:
                            continue
                        href = await link.get_attribute("href")
                        if not href:
                            continue
                        page_url = (
                            href
                            if href.startswith("http")
                            else self._platform.url.rstrip("/") + href
                        )

                        async def _open_additional(_url: str = page_url) -> None:
                            await detail_page.goto(
                                _url, wait_until="domcontentloaded", timeout=45000
                            )
                            await detail_page.wait_for_timeout(3000)

                        await run_with_retry(
                            _open_additional,
                            retry=retry_cfg,
                            circuit=self._site_cb,
                            label=f"Доп. страница {number}",
                        )
                        extra = await extract_from_scope(detail_page, spec.variables)
                        # Не затираем значение основной страницы, если на доп. странице
                        # поле отсутствует (extract_from_scope вернул default=None).
                        detail_vars.update({k: v for k, v in extra.items() if v is not None})
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Доп. страница деталей не обработана: %s", exc)
                # Файлы: если задана отдельная страница файлов (напр. ЕИС documents.html) —
                # переходим на неё (URL = детальный URL с заменой имени html-файла).
                # У 223-ФЗ путь документов иной — переход может не найтись, это не критично.
                files_page = self._platform.detail.files_page
                if files_page:
                    try:

                        async def _open_files() -> None:
                            await detail_page.goto(
                                files_page_url(detail_url, files_page),
                                wait_until="domcontentloaded",
                                timeout=45000,
                            )
                            await detail_page.wait_for_timeout(3000)

                        await run_with_retry(
                            _open_files,
                            retry=retry_cfg,
                            circuit=self._site_cb,
                            label=f"Файлы {number}",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Страница файлов не открылась (%s): %s", files_page, exc)
                files = await detail_files(detail_page, self._platform)
        finally:
            if close_detail and detail_page is not None:
                await detail_page.close()

        record: dict[str, Any] = {**list_vars}
        # Не затираем значения из списка значением None с детальной страницы (например,
        # НМЦК, если детальная SPA не успела отрисовать поле). Аналогично доп. страницам.
        record.update({k: v for k, v in detail_vars.items() if v is not None})
        record["url"] = (
            detail_url
            if detail_url.startswith("http")
            else self._platform.url.rstrip("/") + detail_url
        )
        record["platform_id"] = self._platform_id

        # ИНН заказчика (универсальный механизм, ADR-4). При сбое — None (nullable).
        # Через API (detail.api_format) ИНН приходит прямо в ответе — DOM не нужен.
        # Если ИНН отдаёт уже API списка (например mos.ru) — сохраняем его как есть.
        if api_inn:
            record["inn"] = api_inn
        elif customer_link:
            record["inn"] = await self._resolve_customer_inn(page, customer_link)

        # Активна ли закупка (is_active): не активна, если задан неактивный статус
        # (не входит в active_statuses). Проверка срока актуальности (deadline)
        # выполняется на стороне клиента (репозиторий/API), а не при записи.
        record["is_active"] = self._is_active(record)

        # Клиентская фильтрация (R9) для закупок, где subject в списке был пуст:
        # фильтр по детальным данным (полное описание из карточки деталей).
        if not early_subject and self._client_profile is not None:
            if not keywords_match(record, self._client_keywords):
                logger.info(
                    "Закупка %s отброшена: нет совпадений с ключевыми словами профиля",
                    number,
                )
                return False, number, False
            if exclusions_present(record, self._client_exclusion_words):
                logger.info(
                    "Закупка %s отброшена: слова-исключения в описании",
                    number,
                )
                return False, number, False

        # Stop-условия по срокам (deadline).
        if self._check_stop_conditions(record):
            return False, number, False

        # 5) файлы: парсер НЕ скачивает файлы — сохраняются только метаданные
        #    (имя и URL скачивания с ЭТП). Все файлы, включая ТЗ, — в files_json.
        if files:
            record["files_json"] = files

        # 6) дефолтный скоринг УДАЛЁН: закупка сохраняется без оценки; результат
        #    внешнего каскада приходит через POST /score и пишется в
        #    procurement_evaluations (per-profile, ADR-7).

        # 8) JSONB-карточка формируется из ФИНАЛЬНОЙ записи (включая файлы и
        #    результаты доп. обработки), чтобы снимок соответствовал сохранённому.
        record["detail_json"] = json_safe(record)

        # 9) запись в БД + защита от дубликатов
        saved = await self._persist(record)
        if saved and self._known_numbers is not None:
            self._known_numbers.add(str(number))

        # 9-бис) сохраняем ключевые слова, по которым закупка отобрана профилем (R9):
        # они записываются в procurement_evaluations.matched_keywords ещё до внешнего
        # скоринга (оценка find-or-create обновляется стадиями каскада).
        if (
            saved
            and self._repository is not None
            and self._client_profile is not None
        ):
            hit = matched_keywords(record, self._client_keywords)
            if hit:
                try:
                    await self._repository.record_matched_keywords(
                        int(record["id"]), self._client_profile.id, hit
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Не удалось записать matched_keywords закупки %s: %s",
                        record.get("number"),
                        exc,
                    )

        # 10) авто-пуш задания на внешний скоринг (ADR-7): приоритет очереди — время
        #     обновления/публикации закупки (новые обрабатываются раньше, ZPOPMAX берёт
        #     больший score), как и в recovery (scheduler._recover_scoring_queue).
        #     Уведомление подписчиков отправляется позже — в POST /score, после прихода
        #     внешнего скора и проверки порога notify_min_fit_score (см. api/app.py).
        #     Правила постановки совпадают с правилами записи в БД: в очередь попадает
        #     любая сохранённая закупка, включая просроченные (deadline_not_expired=false).
        if saved and self._transport is not None:
            procurement_id = record.get("id")
            if procurement_id is not None:
                try:
                    ts = record.get("update_date") or record.get("publication_date")
                    priority = self._now.timestamp()
                    if isinstance(ts, datetime):
                        priority = ts.timestamp()
                    elif isinstance(ts, str):
                        with contextlib.suppress(ValueError):
                            priority = datetime.fromisoformat(ts).timestamp()
                    await self._transport.enqueue(int(procurement_id), priority)
                    # Метка успешной постановки (recovery по ней догоняет закупки,
                    # не попавшие в очередь — например, транспорт был недоступен).
                    if self._repository is not None:
                        await self._repository.mark_scoring_queued(int(procurement_id), self._now)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Не удалось поставить задание на скоринг закупки %s: %s",
                        procurement_id,
                        exc,
                    )
        return False, number, saved

    # -- основной цикл ------------------------------------------------------
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
        # только по кодам ОКПД2 (+ обход «без кода»). Критерии поиска берутся из
        # активного профиля (профиль → колонки okpd_codes/nmck_min/nmck_max/active_only);
        # без профиля (dev/тесты) — fallback на глобальный config_service.yaml.
        if self._client_profile is not None:
            base = SearchCriteria(
                okpd_codes=self._client_profile.okpd_codes or [],
                nmck_min=self._client_profile.nmck_min,
                nmck_max=self._client_profile.nmck_max,
                active_only=self._client_profile.active_only,
            )
        else:
            base = self._cfg.service.search_criteria.model_copy(update={"keywords": []})
        # Обход по кодам ОКПД2 имеет смысл, только если площадка реально фильтрует
        # по кодам (есть маппинг okpd2): иначе коды-only обход вернул бы весь список
        # (например roseltorg, где okpd2 не подключён).
        okpd_mapped = bool(search and "okpd2" in (search.criteria_map or {}))
        # Обход «без кода» (R9): клиентская фильтрация словами профиля. Выполняется
        # ОТДЕЛЬНЫМ проходом по всему реестру площадки (фильтр okpdPaths не ставится —
        # пустой список кодов площадка воспринимает как «любой код»), чтобы не терять
        # закупки, подходящие по словам, но вне заданных кодов ОКПД2. Кодовый и
        # «без кода» проходы дополняют друг друга: оба запускаются при наличии
        # позитивных ключевых слов и поиска на площадке.
        has_positive_keywords = bool(self._client_keywords)
        no_code_walk = has_positive_keywords and search is not None
        if not has_positive_keywords:
            logger.info(
                "Площадка %s: в активном профиле нет позитивных ключевых слов — "
                "обход «без кода» пропущен",
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

    async def _crawl(
        self,
        page: Page,
        cutoff: datetime | None,
        criteria: SearchCriteria,
        by_relevance: bool,
        retry_cfg: RetryConfig,
    ) -> None:
        """Обход страниц и записей для одного поискового запроса."""
        search = self._platform.search
        if search is not None and search.api_endpoint:
            # Список читается из JSON API площадки (etpgpb): SSR-страница всегда
            # рендерит базовую выдачу, фильтрует только API после гидрации SPA.
            await self._crawl_api(page, cutoff, criteria, retry_cfg)
            return
        await run_with_retry(
            lambda: open_list_page(page, self._platform, cutoff, criteria),
            retry=retry_cfg,
            circuit=self._site_cb,
            label="Открытие списка",
        )
        await setup_sort_and_filters(page, self._platform, by_relevance=by_relevance)
        await self._delayer.sleep()

        # Ранний пропуск прохода (relevance-режим, без клиентского пост-фильтра):
        # если все результаты поиска уже сохранены в БД (в БД записей не меньше,
        # чем нашёл поиск) — открывать детальные страницы незачем.
        if by_relevance and self._platform.list_config.total_results_selector:
            try:
                search_total = await extract_total_results(page, self._platform)
            except Exception:  # noqa: BLE001
                search_total = None
            if search_total is not None and self._repository is not None:
                db_total = await self._repository.count(self._platform_id)
                if db_total >= search_total:
                    logger.info(
                        "Площадка %s: в БД %d >= результатов поиска %d — новых закупок "
                        "не ожидается, проход пропущен",
                        self._platform_id,
                        db_total,
                        search_total,
                    )
                    return

        # Защита от вечного цикла пагинации (например, когда селектор next_page
        # присутствует и на последней странице): жёсткий потолок числа страниц.
        # 0/None — ограничение отключено.
        max_pages = self._cfg.parser.max_list_pages or 0
        pages_done = 0
        # Статистика обхода (для итоговой сводки «сколько получено с платформы»).
        crawl_received = 0
        crawl_saved = 0
        crawl_known = 0
        # Дополнительная защита от повтора содержимого: площадки, которые за
        # пределами последней страницы возвращают её содержимое повторно (вместо
        # пустой страницы), приводят к бесконечной пагинации с одинаковыми
        # записями. Если набор номеров страницы уже встречался в этом обходе —
        # пагинацию прекращаем (работает и для relevance-сортировки).
        seen_page_sigs: set[frozenset[Any]] = set()

        while True:
            pages_done += 1
            if max_pages and pages_done > max_pages:
                logger.warning(
                    "Площадка %s: превышен лимит страниц %d, обход прерван",
                    self._platform_id,
                    max_pages,
                )
                break

            reached_cutoff = False
            page_total = 0
            page_known = 0
            page_numbers: list[Any] = []
            # Переменная даты публикации — одна на страницу (вне цикла по контейнерам).
            pub_var = next(
                (
                    v
                    for v in self._platform.list_config.variables
                    if v.name == self._platform.list_config.publication_date
                ),
                None,
            )
            async for container in iter_container_records(page, self._platform, self._delayer):
                page_total += 1
                # Выход по порогу даты публикации (только если порог задан). Обрабатываем
                # записи с датой >= дня порога и останавливаемся при записи со строго
                # более ранним днём.
                if pub_var is not None and cutoff is not None:
                    pub = await extract_from_scope(container, [pub_var])
                    pub_val = pub.get(self._platform.list_config.publication_date)
                    older = is_older_than_cutoff(pub_val, cutoff)
                    if older:
                        logger.info(
                            "Достигнут порог дат (%s < %s), завершаем цикл",
                            pub_val,
                            cutoff,
                        )
                        reached_cutoff = True
                        break
                known, number, saved = await self._process_container(page, container)
                if number is not None and number != "":
                    page_numbers.append(number)
                if known:
                    page_known += 1
                crawl_received += 1
                if saved:
                    crawl_saved += 1
                elif known:
                    crawl_known += 1

            # Доcтигли порога дат — завершаем весь проход (не переходим на
            # следующую страницу) и сбрасываем CB.
            if reached_cutoff:
                break

            # Защита от повтора содержимого: тот же набор номеров уже встречался
            # на предыдущей странице этого обхода — площадка зациклила пагинацию
            # (возвращает последнюю страницу повторно). Работает независимо от
            # сортировки (в т.ч. relevance), в отличие от проверки ниже.
            if page_numbers:
                page_sig = frozenset(page_numbers)
                if page_sig in seen_page_sigs:
                    logger.info(
                        "Площадка %s: страница повторяет предыдущую (%d записей) — "
                        "пагинацию прекращаем",
                        self._platform_id,
                        len(page_numbers),
                    )
                    break
                seen_page_sigs.add(page_sig)

            # Ранняя остановка пагинации: вся страница состоит из уже известных закупок.
            # Некоторые площадки (например, lot-online) за пределами последней страницы
            # возвращают её содержимое повторно вместо пустой страницы, поэтому каждая
            # следующая страница была бы одинаковой и не давала бы новых закупок.
            # Актуально только когда известен набор сохранённых номеров (БД доступна) и
            # список отсортирован по дате (не по релевантности): при дата-сортировке порядок
            # страниц монотонный, и страница из одних известных закупок означает, что дальше
            # новых нет. Для релевантности ранняя остановка не применяется, чтобы не
            # пропустить новые закупки на поздних страницах.
            if (
                not by_relevance
                and self._known_numbers is not None
                and page_total > 0
                and page_known == page_total
            ):
                logger.info(
                    "Площадка %s: вся страница уже в БД (%d) — новых закупок не будет, "
                    "пагинацию прекращаем",
                    self._platform_id,
                    page_total,
                )
                break

            # переход на следующую страницу
            if not await next_page_exists(page, self._platform):
                logger.info("Достигнут конец пагинации")
                break
            moved = await run_with_retry(
                lambda: goto_next_page(page, self._platform, self._delayer),
                retry=retry_cfg,
                circuit=self._site_cb,
                label="Следующая страница",
            )
            if not moved:
                logger.info("Не удалось перейти на следующую страницу")
                break
            await self._delayer.sleep()

        self._log_crawl_summary(criteria, crawl_received, crawl_saved, crawl_known)

    async def _crawl_api(
        self,
        page: Page,
        cutoff: datetime | None,
        criteria: SearchCriteria,
        retry_cfg: RetryConfig,
    ) -> None:
        """Обход записей списка через JSON API площадки (``search.api_endpoint``).

        Записи читаются из API напрямую (без DOM-парсинга списка), поля карточки
        берутся из ``attributes`` item'а. Детальные страницы, stop-условия, скоринг
        и сохранение — общий путь ``_process_list_record``.

        Стоп-порог по дате применяется только для обходов без ключевых слов: при
        поиске по словам площадка сортирует по релевантности (``keywords_sort``),
        выдачи маленькие — обходим до конца пагинации.
        """
        lc = self._platform.list_config
        search = self._platform.search
        assert search is not None, "API-обход требует search-конфига площадки"
        page_size = lc.page_size
        has_keywords = bool(criteria.keywords)
        url = build_api_list_url(self._platform, criteria, offset=0)
        page_index = 0
        # Жёсткий потолок числа страниц (защита от вечного цикла).
        max_pages = self._cfg.parser.max_list_pages or 0
        pages_done = 0
        # Статистика обхода (для итоговой сводки «сколько получено с платформы»).
        crawl_received = 0
        crawl_saved = 0
        crawl_known = 0
        seen_page_sigs: set[frozenset[Any]] = set()

        while True:
            pages_done += 1
            if max_pages and pages_done > max_pages:
                logger.warning(
                    "Площадка %s: превышен лимит страниц %d (API), обход прерван",
                    self._platform_id,
                    max_pages,
                )
                break

            items = await run_with_retry(
                partial(fetch_api_items, page, url, search.api_items_key),
                retry=retry_cfg,
                circuit=self._site_cb,
                label="API список",
            )
            if not items:
                logger.info("API списка вернул пустую страницу — конец пагинации")
                break

            reached_cutoff = False
            page_total = 0
            page_known = 0
            page_numbers: list[Any] = []
            for item in items:
                page_total += 1
                list_vars = parse_api_item(item, search.api_item_format)
                number = list_vars.get("number")
                # Оптимизация повторного прохода: закупка уже в БД — детальную
                # страницу не открываем (как в DOM-обходе _process_container).
                if self._is_known(number):
                    logger.info("Закупка %s уже в БД — пропуск", number)
                    page_known += 1
                    crawl_known += 1
                    crawl_received += 1
                    if number is not None and number != "":
                        page_numbers.append(number)
                    continue
                # Стоп-порог по дате (только не keyword-обходы: там relevance-сортировка).
                if (
                    not has_keywords
                    and cutoff is not None
                    and is_older_than_cutoff(list_vars.get(lc.publication_date), cutoff)
                ):
                    logger.info(
                        "Достигнут порог дат (%s < %s), завершаем цикл",
                        list_vars.get(lc.publication_date),
                        cutoff,
                    )
                    reached_cutoff = True
                    break
                detail_path = list_vars.pop("detail_path", None)
                # Поля для извлечения деталей через API (детальная страница не
                # открывается): id площадки и т.п., не попадают в запись.
                api_fields = list_vars.pop("_api", None) or None
                detail_url = None
                if detail_path:
                    detail_url = (
                        detail_path
                        if detail_path.startswith("http")
                        else self._platform.url.rstrip("/") + detail_path
                    )
                known, number, saved = await self._process_list_record(
                    page,
                    list_vars,
                    detail_url,
                    number,
                    api_fields=api_fields,
                )
                if number is not None and number != "":
                    page_numbers.append(number)
                if known:
                    page_known += 1
                crawl_received += 1
                if saved:
                    crawl_saved += 1
                elif known:
                    crawl_known += 1

            if reached_cutoff:
                break

            # Защита от повтора содержимого: тот же набор номеров на предыдущей странице.
            if page_numbers:
                page_sig = frozenset(page_numbers)
                if page_sig in seen_page_sigs:
                    logger.info(
                        "Площадка %s: страница повторяет предыдущую (%d записей) — "
                        "пагинацию прекращаем",
                        self._platform_id,
                        len(page_numbers),
                    )
                    break
                seen_page_sigs.add(page_sig)

            # Вся страница из уже известных закупок (дата-сортировка) — новых не будет.
            if (
                not has_keywords
                and self._known_numbers is not None
                and page_total > 0
                and page_known == page_total
            ):
                logger.info(
                    "Площадка %s: вся страница уже в БД (%d) — новых закупок не будет, "
                    "пагинацию прекращаем",
                    self._platform_id,
                    page_total,
                )
                break

            # Неполная страница (< page_size) — последняя.
            if page_size and len(items) < page_size:
                logger.info(
                    "API списка: страница %d вернула %d записей (меньше page_size=%d) — "
                    "это последняя страница, пагинация завершена",
                    pages_done,
                    len(items),
                    page_size,
                )
                break

            # Пагинация: либо перестройка URL с новым offset (api_offset_param —
            # take/skip внутри JSON-параметра, mos.ru), либо инкремент плоского
            # параметра (page/offset) в текущем URL.
            page_index += 1
            if search.api_offset_param:
                step = search.api_offset_step or 1
                url = build_api_list_url(self._platform, criteria, offset=page_index * step)
            else:
                page_param = lc.page_param or "page"
                step = search.api_offset_step or 1
                url = _increment_url_page(url, page_param, step=step)
            await self._delayer.sleep()

        self._log_crawl_summary(criteria, crawl_received, crawl_saved, crawl_known)

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
        if criteria.keywords:
            scope = f"слова: {criteria.keywords}"
        elif criteria.okpd_codes:
            scope = f"коды ОКПД2: {criteria.okpd_codes}"
        else:
            scope = "весь список"
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
