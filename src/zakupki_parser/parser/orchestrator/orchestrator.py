"""Оркестратор основного алгоритма парсинга одной площадки.

См. specification.md для детального описания шагов. Вспомогательные миксины:
``activity`` (активность), ``stop`` (условия прекращения), ``persistence`` (запись в БД).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
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
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.parser.lister import (
    extract_total_results,
    goto_next_page,
    iter_container_records,
    next_page_exists,
    open_list_page,
    setup_sort_and_filters,
)
from zakupki_parser.parser.orchestrator.activity import ActivityMixin
from zakupki_parser.parser.orchestrator.persistence import PersistenceMixin
from zakupki_parser.parser.orchestrator.stop import StopMixin
from zakupki_parser.parser.organization import capture_customer_link, resolve_inn
from zakupki_parser.retry import run_with_retry
from zakupki_parser.scoring import ScoringTransportClient, score_for_record
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

    async def _process_container(self, page: Page, container: Locator) -> bool:
        """Обрабатывает один контейнер записи о закупке.

        Возвращает True, если запись пропущена как уже сохранённая в БД (повтор),
        иначе — False.
        """
        # 1) list-vars
        list_vars = await extract_from_scope(container, self._platform.list_config.variables)
        number = list_vars.get("number")

        # Оптимизация повторного прохода: закупка уже в БД — детальную страницу
        # не открываем (upsert не обновляет известные записи, поведение не меняется).
        if self._is_known(number):
            logger.info("Закупка %s уже в БД, детали не обрабатываем", number)
            return True

        # 2) ссылка на детальную страницу
        detail_link_loc = container.locator(self._platform.list_config.detail_link)
        if await detail_link_loc.count() == 0:
            logger.debug("Нет ссылки на детали, пропуск (number=%s)", number)
            return False
        detail_url = await detail_link_loc.first.get_attribute("href")
        if not detail_url:
            return False

        # stop-условия по данным из деталей проверяются после извлечения деталей.
        # 3) переход на детальную страницу — в отдельной вкладке, чтобы не терять
        #    страницу списка (итерация по контейнерам и пагинация продолжаются).
        #    «Возврат к списку» (п.10 ТЗ) — закрытие этой вкладки.
        detail_page: Page
        close_detail = False
        files: list[dict[str, str]] = []
        customer_link: str | None = None
        if self._new_page is not None:
            detail_page = await self._new_page()
            close_detail = True
        else:
            detail_page = page
        try:
            retry_cfg = self._cfg.parser.retry
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
                        href if href.startswith("http") else self._platform.url.rstrip("/") + href
                    )

                    async def _open_additional(_url: str = page_url) -> None:
                        await detail_page.goto(_url, wait_until="domcontentloaded", timeout=45000)
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
            if close_detail:
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
        record["source_platform"] = self._platform_id

        # ИНН заказчика (универсальный механизм, ADR-4). При сбое — None (nullable).
        record["inn"] = await self._resolve_customer_inn(page, customer_link)

        # Активна ли закупка (is_active): не активна, если задан неактивный статус
        # (не входит в active_statuses) ИЛИ истёк срок актуальности (deadline < now).
        record["is_active"] = self._is_active(record)

        # 4) условия прекращения обработки
        if self._check_stop_conditions(record):
            return False

        # 5) файлы: парсер НЕ скачивает файлы — сохраняются только метаданные
        #    (имя и URL скачивания с ЭТП). Все файлы, включая ТЗ, — в files_json.
        if files:
            record["files_json"] = files

        # 6) скоринг закупки (Score = Fit × P(win) × Margin).
        #    Просроченный срок подачи заявок -> score=0, score_method=deadline_expired.
        #    Финальный внешний score проставит конвейер скоринга через POST /score (ADR-7).
        if "score" not in record:
            score, fit_score, method = await score_for_record(
                record,
                self._cfg.score,
                self._now,
                active_only=self._cfg.service.search_criteria.active_only,
            )
            record["score"] = score
            record["fit_score"] = fit_score
            record["score_method"] = method

        # 8) JSONB-карточка формируется из ФИНАЛЬНОЙ записи (включая файлы, score,
        #    результаты доп. обработки), чтобы снимок соответствовал сохранённому.
        record["detail_json"] = json_safe(record)

        # 9) запись в БД + защита от дубликатов
        saved = await self._persist(record)
        if saved and self._known_numbers is not None:
            self._known_numbers.add(str(number))

        # 10) авто-пуш задания на внешний скоринг (ADR-7): закупка сохранена с дефолтным
        #     скором; transport ставит её в приоритетную очередь. Уведомление подписчиков
        #     отправляется позже — в POST /score, после прихода внешнего скора и проверки
        #     порога notify_min_fit_score (см. api/app.py). deadline_expired не скорим (скор=0).
        if saved and self._transport is not None and record.get("score_method") == "default":
            procurement_id = record.get("id")
            if procurement_id is not None:
                try:
                    await self._transport.enqueue(
                        int(procurement_id), float(record.get("score") or 0.0)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Не удалось поставить задание на скоринг закупки %s: %s",
                        procurement_id,
                        exc,
                    )
        return False

    # -- основной цикл ------------------------------------------------------
    async def run(self, page: Page) -> None:
        """Запускает проход по площадке на заданной ``page``."""
        if not self._site_cb.allow_request():
            raise CircuitOpenError("Сайт недоступен (circuit open)")

        by_relevance = bool(self._platform.sort and self._platform.sort.by_relevance)
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
            # По умолчанию порог берётся из даты последней обработанной записи БД;
            # default_cutoff_days применяется только когда в БД ещё нет записей.
            cutoff = await self._repository.last_processed_date(
                self._platform_id, self._now, self._cfg.service.default_cutoff_days
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
        keywords = self._cfg.service.search_criteria.keywords
        # Слова по «ИЛИ» — только если площадка реально использует keywords
        # (есть маппинг критерия keywords): иначе перебор слов дал бы одинаковые обходы
        # (например etpgpb, где procedure[name] не фильтрует).
        keywords_mapped = bool(search and "keywords" in (search.criteria_map or {}))
        one_at_a_time = bool(
            search and search.keywords_one_at_a_time and keywords and keywords_mapped
        )

        if one_at_a_time:
            # Площадка объединяет слова по «И» (AND), OR-оператора нет (B2B-Center,
            # fabrikant): перебираем слова по одному, результаты объединяются (дедуп по
            # номеру закупки через self._known_numbers, пополняемый при сохранении).
            # Коды ОКПД2 всегда обрабатываются отдельно (keywords_codes удалён —
            # слова и коды ищутся независимо, OR).
            logger.info(
                "Площадка %s: поиск по словам по одному (AND на площадке) — %d слов, коды: %s",
                self._platform_id,
                len(keywords),
                self._cfg.service.search_criteria.okpd_codes,
            )
            base = self._cfg.service.search_criteria
            for word in keywords:
                criteria = base.model_copy(update={"keywords": [word], "okpd_codes": []})
                await self._crawl(page, cutoff, criteria, by_relevance, retry_cfg)
            if base.okpd_codes:
                criteria = base.model_copy(update={"keywords": []})
                await self._crawl(page, cutoff, criteria, by_relevance, retry_cfg)
        else:
            base = self._cfg.service.search_criteria
            if base.keywords and base.okpd_codes:
                # Расширение (OR): слова (без кодов) + коды (без слов) — объединение.
                logger.info(
                    "Площадка %s: слова и коды ищутся независимо — словами %s, кодами %s",
                    self._platform_id,
                    base.keywords,
                    base.okpd_codes,
                )
                await self._crawl(
                    page,
                    cutoff,
                    base.model_copy(update={"okpd_codes": []}),
                    by_relevance,
                    retry_cfg,
                )
                await self._crawl(
                    page, cutoff, base.model_copy(update={"keywords": []}), by_relevance, retry_cfg
                )
            else:
                await self._crawl(
                    page, cutoff, self._cfg.service.search_criteria, by_relevance, retry_cfg
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
        await run_with_retry(
            lambda: open_list_page(page, self._platform, cutoff, criteria),
            retry=retry_cfg,
            circuit=self._site_cb,
            label="Открытие списка",
        )
        await setup_sort_and_filters(page, self._platform)
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
            async for container in iter_container_records(page, self._platform, self._delayer):
                page_total += 1
                # Выход по порогу даты публикации (только если порог задан). Обрабатываем
                # записи с датой >= дня порога и останавливаемся при записи со строго
                # более ранним днём.
                pub_var = next(
                    (
                        v
                        for v in self._platform.list_config.variables
                        if v.name == self._platform.list_config.publication_date
                    ),
                    None,
                )
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
                if await self._process_container(page, container):
                    page_known += 1

            # Доcтигли порога дат — завершаем весь проход (не переходим на
            # следующую страницу) и сбрасываем CB.
            if reached_cutoff:
                break

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
