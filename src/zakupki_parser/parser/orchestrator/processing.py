"""Обработка одной записи списка (DOM/API): детали, stop-условия, скоринг, запись.

Выделено из прежнего ``parser/orchestrator/orchestrator.py``: метод
``_process_list_record`` класса Orchestrator перенесён в миксин
``RecordProcessingMixin`` без изменения логики.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from functools import partial
from typing import Any

from playwright.async_api import Page

from zakupki_parser.parser.detail import (
    detail_files,
    extract_detail_vars,
    files_page_url,
    open_detail,
)
from zakupki_parser.parser.detail_api import fetch_api_details
from zakupki_parser.parser.extractor import extract_from_scope
from zakupki_parser.parser.filtering import (
    exclusions_present,
    keywords_match,
    matched_keywords,
)
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.parser.orchestrator.state import OrchestratorState
from zakupki_parser.parser.organization import capture_customer_link
from zakupki_parser.retry import run_with_retry

# Имя логгера сохранено прежним (категория модуля orchestrator).
logger = logging.getLogger("zakupki_parser.parser.orchestrator.orchestrator")


class RecordProcessingMixin(OrchestratorState):
    """Обработка одной записи из списка (детали, фильтр, запись, пуш в скоринг)."""

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
        if saved and self._repository is not None and self._client_profile is not None:
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
