"""Оркестратор основного алгоритма парсинга одной площадки.

См. specification.md для детального описания шагов.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Locator, Page
from sqlalchemy.exc import IntegrityError

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import AppConfig, PlatformDom
from zakupki_parser.downloader import download_files, split_technical_spec
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
    goto_next_page,
    iter_container_records,
    next_page_exists,
    open_list_page,
    setup_sort_and_filters,
)
from zakupki_parser.scoring import ExternalScoreClient, score_for_record
from zakupki_parser.storage.db_errors import is_data_db_error, is_transient_db_error
from zakupki_parser.storage.last_seen import LastSeenStore
from zakupki_parser.storage.object_store import FileRef, build_object_store
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
        last_seen: LastSeenStore,
        site_cb: CircuitBreaker,
        db_cb: CircuitBreaker,
        new_page: Callable[[], Awaitable[Page]] | None = None,
        now: datetime | None = None,
    ) -> None:
        self._cfg = cfg
        self._platform_id = platform_id
        self._platform = platform
        self._delayer = delayer
        self._repository = repository
        self._notifier = notifier
        self._last_seen = last_seen
        self._site_cb = site_cb
        self._db_cb = db_cb
        self._new_page = new_page
        self._now = now or datetime.now(UTC)
        self._object_store = build_object_store(
            cfg.service.storage, Path(cfg.service.documents_dir).resolve()
        )
        self._external_scorer: ExternalScoreClient | None = (
            ExternalScoreClient(cfg.score)
            if cfg.score.method == "external" and cfg.score.external_call_mode == "before_save"
            else None
        )

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
        Транзиентные ошибки повторяются с линейным backoff до исчерпания попыток.
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
                    delay = db_cfg.retry_backoff_seconds * attempt
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
        if self._new_page is not None:
            detail_page = await self._new_page()
            close_detail = True
        else:
            detail_page = page
        try:
            await open_detail(detail_page, detail_url, self._platform)
            detail_vars = await extract_detail_vars(detail_page, self._platform)
            # Файлы: если задана отдельная страница файлов (напр. ЕИС documents.html) —
            # переходим на неё (URL = детальный URL с заменой имени html-файла).
            files_page = self._platform.detail.files_page
            if files_page:
                await detail_page.goto(
                    files_page_url(detail_url, files_page),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await detail_page.wait_for_timeout(3000)
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

        # 4) условия прекращения обработки
        if self._check_stop_conditions(record):
            return

        # 5) файлы: имена и URL скачивания с ЭТП сохраняются в БД (скачивание
        #    по умолчанию НЕ выполняется). ТЗ — два отдельных поля, остальные —
        #    files_json (список пар name/url).
        keywords = self._cfg.service.technical_spec_keywords
        ts_files, other_files = split_technical_spec(files, keywords)
        if ts_files:
            record["technical_spec_name"] = ts_files[0]["name"]
            record["technical_spec_url"] = ts_files[0]["url"]
        if other_files:
            record["files_json"] = other_files

        # 6) скачивание файлов в хранилище (опционально, не основной режим).
        downloaded: list[FileRef] = []
        if self._cfg.service.download_files:
            to_download = ts_files if self._cfg.service.download_technical_spec_only else files
            urls = [f["url"] for f in to_download]
            try:
                downloaded = await download_files(
                    page,
                    self._platform,
                    self._object_store,
                    str(number),
                    urls,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ошибка скачивания файлов заявки %s: %s", number, exc)
            # Ключ сохранённого ТЗ (если ТЗ было среди скачанных).
            if ts_files and downloaded:
                try:
                    idx = urls.index(ts_files[0]["url"])
                    record["technical_spec_key"] = downloaded[idx].key
                except ValueError:
                    pass

        # 7) удаление скачанных файлов (по флагу). ТЗ сохраняется — на него
        #    ссылается БД (technical_spec_key). Обработку файлов (извлечение
        #    переменных) выполняет внешний сервис.
        if self._cfg.service.download_files and self._cfg.service.delete_files_after_processing:
            kept = {record.get("technical_spec_key")}
            for ref in downloaded:
                if ref.key in kept:
                    logger.debug("ТЗ сохраняется: %s", ref.key)
                    continue
                try:
                    await self._object_store.delete(ref.key)
                    logger.debug("Удалён файл %s", ref.key)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Не удалось удалить %s: %s", ref.key, exc)

        # 8) скоринг закупки (Score = Fit × P(win) × Margin).
        #    Просроченный срок подачи заявок -> score=0, score_method=deadline_expired.
        if "score" not in record:
            score, method = await score_for_record(
                record, self._cfg.score, self._external_scorer, self._now
            )
            record["score"] = score
            record["score_method"] = method

        # 9) JSONB-карточка формируется из ФИНАЛЬНОЙ записи (включая файлы, score,
        #    результаты доп. обработки), чтобы снимок соответствовал сохранённому.
        record["detail_json"] = json_safe(record)

        # 10) запись в БД + защита от дубликатов
        saved = await self._persist(record)

        # 11) webhook только для новых записей
        if saved:
            await self._notifier.notify(record)

    # -- основной цикл ------------------------------------------------------
    async def run(self, page: Page) -> None:
        """Запускает проход по площадке на заданной ``page``."""
        if not self._site_cb.allow_request():
            raise CircuitOpenError("Сайт недоступен (circuit open)")

        cutoff = self._last_seen.load(self._platform_id, self._now)
        logger.info("Начало обработки площадки %s, порог даты: %s", self._platform_id, cutoff)

        await open_list_page(
            page,
            self._platform,
            cutoff,
            self._cfg.service.search_criteria,
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
            # следующую страницу), чтобы обновить last_seen и сбросить CB.
            if reached_cutoff:
                break

            # переход на следующую страницу
            if not await next_page_exists(page, self._platform):
                logger.info("Достигнут конец пагинации")
                break
            moved = await goto_next_page(page, self._platform, self._delayer)
            if not moved:
                logger.info("Не удалось перейти на следующую страницу")
                break
            await self._delayer.sleep()

        # обновляем дату последней обработки
        self._last_seen.save(self._platform_id, self._now)
        self._site_cb.record_success()
