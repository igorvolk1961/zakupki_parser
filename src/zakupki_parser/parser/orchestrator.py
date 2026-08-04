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

import asyncpg
from playwright.async_api import Locator, Page
from sqlalchemy.exc import DBAPIError, IntegrityError

from zakupki_parser.browser.delayer import Delayer
from zakupki_parser.circuit import CircuitBreaker, CircuitOpenError
from zakupki_parser.config.models import (
    AppConfig,
    PlatformDom,
)
from zakupki_parser.downloader import download_files
from zakupki_parser.file_processor import FileProcessor
from zakupki_parser.notify import Notifier
from zakupki_parser.parser.detail import detail_file_urls, extract_detail_vars, open_detail
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.lister import (
    goto_next_page,
    iter_container_records,
    next_page_exists,
    open_list_page,
    setup_sort_and_filters,
)
from zakupki_parser.storage.last_seen import LastSeenStore
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


def is_older_than_cutoff(upd: str | datetime | None, cutoff: datetime) -> bool | None:
    """Проверяет, должна ли запись остановить цикл по дате публикации.

    На площадке доступна только дата (без времени), поэтому сравнение ведётся по
    календарному дню: запись «старее» порога — когда её день строго меньше дня
    порога. Возвращает True — стоп, False — обрабатывать далее, None — некорректная
    дата (обрабатывать).
    """
    if upd is None:
        return None
    if isinstance(upd, str):
        try:
            upd_dt = datetime.fromisoformat(upd)
        except ValueError:
            return None
    else:
        upd_dt = upd
    return upd_dt.date() < cutoff.date()


def _json_safe(data: Any) -> Any:
    """Рекурсивно приводит datetime к ISO-строке (для JSONB)."""
    if isinstance(data, dict):
        return {k: _json_safe(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_json_safe(v) for v in data]
    if isinstance(data, datetime):
        return data.isoformat()
    return data


def _unwrap_db_error(exc: BaseException) -> BaseException:
    """Распаковывает SQLAlchemy DBAPIError до исходного (asyncpg) исключения."""
    while isinstance(exc, DBAPIError) and exc.orig is not None:
        exc = exc.orig
    return exc


def _is_transient_db_error(exc: BaseException) -> bool:
    """Транзиентная ошибка (недоступность БД/сети) — учитывается circuit breaker'ом."""
    exc = _unwrap_db_error(exc)
    return isinstance(
        exc,
        (
            asyncpg.PostgresConnectionError,
            asyncpg.InterfaceError,
            OSError,
            TimeoutError,
        ),
    )


def _is_data_db_error(exc: BaseException) -> bool:
    """Ошибка данных/схемы (не транзиентная) — НЕ учитывается circuit breaker'ом."""
    exc = _unwrap_db_error(exc)
    return isinstance(exc, asyncpg.DataError)


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
        file_processor: FileProcessor,
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
        self._file_processor = file_processor
        self._last_seen = last_seen
        self._site_cb = site_cb
        self._db_cb = db_cb
        self._new_page = new_page
        self._now = now or datetime.now(UTC)

    # -- приватные помощники -------------------------------------------------
    @staticmethod
    def _documents_dir(cfg: AppConfig) -> Path:
        return Path(cfg.service.documents_dir).resolve()

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
                logger.info("Дубликат по unique-констрейнту: %s", exc)
                return False
            except Exception as exc:  # noqa: BLE001
                if _is_data_db_error(exc):
                    logger.error("Ошибка данных при записи заявки: %s", exc)
                    return False
                if _is_transient_db_error(exc) and attempt < attempts:
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
        file_urls: list[str] = []
        if self._new_page is not None:
            detail_page = await self._new_page()
            close_detail = True
        else:
            detail_page = page
        try:
            await open_detail(detail_page, detail_url, self._platform)
            detail_vars = await extract_detail_vars(detail_page, self._platform)
            file_urls = await detail_file_urls(detail_page, self._platform)
        finally:
            if close_detail:
                await detail_page.close()

        record: dict[str, Any] = {**list_vars, **detail_vars}
        record["url"] = self._platform.url.rstrip("/") + detail_url
        record["source_platform"] = self._platform_id
        record["detail_json"] = _json_safe(record)

        # 4) условия прекращения обработки
        if self._check_stop_conditions(record):
            return

        # 5) скачивание файлов (URL собраны с детальной страницы)
        downloaded: list[Path] = []
        if self._cfg.service.download_files:
            try:
                downloaded = await download_files(
                    page,
                    self._platform,
                    self._documents_dir(self._cfg),
                    str(number),
                    file_urls,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ошибка скачивания файлов заявки %s: %s", number, exc)

        # 6) доп. обработка файлов (заглушка)
        extracted = await self._file_processor.process(downloaded, str(number))
        if extracted:
            record.update(extracted)

        # 7) удаление файлов (по флагу) + удаление опустевшей папки заявки
        if self._cfg.service.delete_files_after_processing:
            for path in downloaded:
                try:
                    if path.is_file():
                        path.unlink()
                        logger.debug("Удалён файл %s", path)
                except OSError as exc:
                    logger.warning("Не удалось удалить %s: %s", path, exc)
            if downloaded:
                target_dir = downloaded[0].parent
                try:
                    target_dir.rmdir()  # удаляется только пустая папка
                    logger.debug("Удалена пустая папка %s", target_dir)
                except OSError:
                    logger.debug("Папка %s не удалена (не пуста или отсутствует)", target_dir)

        # 8) запись в БД + защита от дубликатов
        saved = await self._persist(record)

        # 9) webhook только для новых записей
        if saved:
            await self._notifier.notify(record)

    # -- основной цикл ------------------------------------------------------
    async def run(self, page: Page) -> None:
        """Запускает проход по площадке на заданной ``page``."""
        if not self._site_cb.allow_request():
            raise CircuitOpenError("Сайт недоступен (circuit open)")

        cutoff = self._last_seen.load(self._platform_id, self._now)
        logger.info("Начало обработки площадки %s, порог даты: %s", self._platform_id, cutoff)

        await open_list_page(page, self._platform, cutoff)
        await setup_sort_and_filters(page, self._platform)
        await self._delayer.sleep()

        while True:
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
                        return
                await self._process_container(page, container)

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
