"""Оркестратор основного алгоритма парсинга одной площадки.

См. specification.md для детального описания шагов.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from playwright.async_api import Locator, Page
from sqlalchemy.exc import IntegrityError

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.cutoff import is_older_than_cutoff
from zakupki_parser.parser.detail import (
    detail_files,
    extract_detail_vars,
    files_page_url,
    open_detail,
)
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.files import split_technical_spec
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.parser.lister import (
    goto_next_page,
    iter_container_records,
    next_page_exists,
    open_list_page,
    setup_sort_and_filters,
)
from zakupki_parser.parser.organization import capture_customer_link, resolve_inn
from zakupki_parser.retry import run_with_retry
from zakupki_parser.scoring import score_for_record
from zakupki_parser.storage.db_errors import is_data_db_error, is_transient_db_error
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class Orchestrator:
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
        # Кеш ИНН по ссылке на организацию: страницу организации грузим не чаще раза за проход.
        self._inn_cache: dict[str, str | None] = {}

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

    # -- приватные помощники -------------------------------------------------
    def _check_stop_conditions(self, record: dict[str, Any]) -> bool:
        """Проверяет набор флагов прекращения обработки заявки.

        Возвращает True, если заявку следует ПРОПУСТИТЬ (обработка прекращается).
        """
        sc = self._cfg.service.stop_conditions
        if not sc.enabled:
            return False
        if sc.deadline_not_expired:
            deadline = record.get("deadline")
            if isinstance(deadline, datetime) and deadline < self._now:
                logger.info(
                    "Заявка %s пропущена: срок приёма истёк (%s)",
                    record.get("number"),
                    deadline,
                )
                return True
        if sc.min_deadline_days is not None:
            deadline = record.get("deadline")
            if isinstance(deadline, datetime):
                days_left = (deadline - self._now).total_seconds() / 86400
                if days_left < sc.min_deadline_days:
                    logger.info(
                        "Заявка %s пропущена: до срока подачи %.1f дн. < %d",
                        record.get("number"),
                        days_left,
                        sc.min_deadline_days,
                    )
                    return True
        return False

    async def _persist(self, record: dict[str, Any]) -> bool:
        """Сохраняет заявку в БД с вежливой деградацией.

        Circuit breaker учитывает ТОЛЬКО транзиентные ошибки доступности БД;
        ошибки данных/схемы (например, усечение значения) не открывают CB.
        Транзиентные ошибки повторяются с экспоненциальным backoff
        (base × 2^(n-1)) до исчерпания попыток.
        """
        if not self._cfg.service.db.enabled or self._repository is None:
            return False
        if not self._db_cb.allow_request():
            logger.warning("БД недоступна (circuit open), запись пропущена")
            return False

        db_cfg = self._cfg.service.db
        attempts = db_cfg.retry_max_attempts
        for attempt in range(1, attempts + 1):
            try:
                saved = await self._repository.upsert(record)
                self._db_cb.record_success()
                if saved and self._on_record_saved is not None:
                    await self._on_record_saved()
                return saved
            except IntegrityError as exc:
                # Конкурентная вставка того же номера — не ошибка доступности.
                # БД доступна, поэтому сбрасываем счётчик отказов CB.
                logger.info("Дубликат по unique-констрейнту: %s", exc)
                self._db_cb.record_success()
                return False
            except Exception as exc:  # noqa: BLE001
                if is_data_db_error(exc):
                    logger.error("Ошибка данных при записи заявки: %s", exc)
                    return False
                if is_transient_db_error(exc) and attempt < attempts:
                    delay = db_cfg.retry_backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "Транзиентная ошибка БД (%s), retry %d/%d через %.1f с",
                        exc,
                        attempt,
                        attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                self._db_cb.record_failure()
                logger.error("Ошибка записи в БД: %s", exc)
                return False
        return False

    async def _process_container(self, page: Page, container: Locator) -> None:
        """Обрабатывает один контейнер записи о закупке."""
        # 1) list-vars
        list_vars = await extract_from_scope(container, self._platform.list_config.variables)
        number = list_vars.get("number")

        # 2) ссылка на детальную страницу
        detail_link_loc = container.locator(self._platform.list_config.detail_link)
        if await detail_link_loc.count() == 0:
            logger.debug("Нет ссылки на детали, пропуск (number=%s)", number)
            return
        detail_url = await detail_link_loc.first.get_attribute("href")
        if not detail_url:
            return

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

        record: dict[str, Any] = {**list_vars, **detail_vars}
        record["url"] = (
            detail_url
            if detail_url.startswith("http")
            else self._platform.url.rstrip("/") + detail_url
        )
        record["source_platform"] = self._platform_id

        # ИНН заказчика (универсальный механизм, ADR-4). При сбое — None (nullable).
        record["inn"] = await self._resolve_customer_inn(page, customer_link)

        # 4) условия прекращения обработки
        if self._check_stop_conditions(record):
            return

        # 5) файлы: парсер НЕ скачивает файлы — сохраняются только метаданные
        #    (имя и URL скачивания с ЭТП). ТЗ — два отдельных поля, остальные — files_json.
        ts_files, other_files = split_technical_spec(files)
        if ts_files:
            record["technical_spec_name"] = ts_files[0]["name"]
            record["technical_spec_url"] = ts_files[0]["url"]
        if other_files:
            record["files_json"] = other_files

        # 6) скоринг закупки (Score = Fit × P(win) × Margin).
        #    Просроченный срок подачи заявок -> score=0, score_method=deadline_expired.
        #    Финальный внешний score проставит конвейер скоринга через POST /score (ADR-7).
        if "score" not in record:
            score, method = await score_for_record(record, self._cfg.score, self._now)
            record["score"] = score
            record["score_method"] = method

        # 8) JSONB-карточка формируется из ФИНАЛЬНОЙ записи (включая файлы, score,
        #    результаты доп. обработки), чтобы снимок соответствовал сохранённому.
        record["detail_json"] = json_safe(record)

        # 9) запись в БД + защита от дубликатов
        saved = await self._persist(record)

        # 10) webhook только для новых записей
        if saved:
            await self._notifier.notify(record)

    # -- основной цикл ------------------------------------------------------
    async def run(self, page: Page) -> None:
        """Запускает проход по площадке на заданной ``page``."""
        if not self._site_cb.allow_request():
            raise CircuitOpenError("Сайт недоступен (circuit open)")

        if self._repository is None:
            cutoff = self._now - timedelta(days=self._cfg.service.default_cutoff_days)
        else:
            cutoff = await self._repository.last_processed_date(
                self._platform_id, self._now, self._cfg.service.default_cutoff_days
            )
        logger.info("Начало обработки площадки %s, порог даты: %s", self._platform_id, cutoff)

        retry_cfg = self._cfg.parser.retry
        await run_with_retry(
            lambda: open_list_page(page, self._platform, cutoff, self._cfg.service.search_criteria),
            retry=retry_cfg,
            circuit=self._site_cb,
            label="Открытие списка",
        )
        await setup_sort_and_filters(page, self._platform)
        await self._delayer.sleep()

        while True:
            reached_cutoff = False
            async for container in iter_container_records(page, self._platform, self._delayer):
                # Выход по порогу даты публикации. Обрабатываем записи с датой >=
                # дня порога и останавливаемся при записи со строго более ранним днём.
                pub_var = next(
                    (
                        v
                        for v in self._platform.list_config.variables
                        if v.name == self._platform.list_config.publication_date
                    ),
                    None,
                )
                if pub_var is not None:
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
                await self._process_container(page, container)

            # Доcтигли порога дат — завершаем весь проход (не переходим на
            # следующую страницу) и сбрасываем CB.
            if reached_cutoff:
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

        self._site_cb.record_success()
