"""Обход страниц и записей для одного поискового запроса (DOM и API-обход).

Выделено из прежнего ``parser/orchestrator/orchestrator.py``: методы ``_crawl``/
``_crawl_api`` класса Orchestrator перенесены в миксин ``CrawlMixin`` без
изменения логики.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial
from typing import Any

from playwright.async_api import Page

from zakupki_parser.config.models import RetryConfig, SearchCriteria
from zakupki_parser.parser.cutoff import is_older_than_cutoff
from zakupki_parser.parser.extractor import extract_from_scope
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
from zakupki_parser.parser.orchestrator.state import OrchestratorState
from zakupki_parser.retry import run_with_retry

# Имя логгера сохранено прежним (категория модуля orchestrator).
logger = logging.getLogger("zakupki_parser.parser.orchestrator.orchestrator")


class CrawlMixin(OrchestratorState):
    """Обход списка закупок: DOM-страницы или JSON API площадки."""

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

        Стоп-порог по дате применяется всегда (сортировка по дате); обход идёт
        до записи старше порога или до конца пагинации.
        """
        lc = self._platform.list_config
        search = self._platform.search
        assert search is not None, "API-обход требует search-конфига площадки"
        page_size = lc.page_size
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
                # Для мультипрофильного прохода пропуск невозможен: запись нужна
                # каждому профилю (у другого профиля может ещё не быть оценки).
                if self._is_known(number) and not self._multi_run:
                    logger.info("Закупка %s уже в БД — пропуск", number)
                    page_known += 1
                    crawl_known += 1
                    crawl_received += 1
                    if number is not None and number != "":
                        page_numbers.append(number)
                    continue
                # Стоп-порог по дате.
                if cutoff is not None and is_older_than_cutoff(
                    list_vars.get(lc.publication_date), cutoff
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
            if self._known_numbers is not None and page_total > 0 and page_known == page_total:
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
