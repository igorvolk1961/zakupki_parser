"""Обработка одной записи списка (DOM/API): фильтр, запись, скоринг, детали.

Выделено из прежнего ``parser/orchestrator/orchestrator.py``: метод
``_process_list_record`` класса Orchestrator перенесён в миксин
``RecordProcessingMixin`` без изменения логики. С BR-08 платформенные детали
(ОКПД2/файлы/ИНН) не запрашиваются до скоринга: запись идёт по данным уровня
списка, а детали дособираются отдельным best-effort проходом
``_collect_pending_details`` ТОЛЬКО ПОСЛЕ получения результата скоринга
(``procurement_evaluations.fit_score IS NOT NULL``; сбой деталей не роняет проход).
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

        ctxs = self._profile_ctxs
        multi = len(ctxs) > 1
        early_subject = str(list_vars.get("subject") or "")
        # Ранняя клиентская фильтрация (R9) — только для одиночного профиля: subject
        # уже есть в карточке списка, применяем слова ДО запроса деталей, чтобы не
        # тратить лимиты API площадки на заведомо неподходящие закупки (mos.ru 402).
        # Для мультипрофильного обхода ранний фильтр невозможен: запись нужна каждому
        # профилю, слова применяются после получения записи (цикл по ctxs ниже).
        if early_subject and not multi and ctxs:
            first = ctxs[0]
            if not keywords_match(list_vars, first.keywords):
                logger.info(
                    "Закупка %s отброшена: нет совпадений с ключевыми словами профиля",
                    number,
                )
                return False, number, False
            if exclusions_present(list_vars, first.exclusion_words):
                logger.info(
                    "Закупка %s отброшена: слова-исключения в описании",
                    number,
                )
                return False, number, False

        # 3) детали ПЕРЕНЕСЕНЫ в отдельный проход ПОСЛЕ получения результата скоринга
        #    (BR-08). Здесь фиксируем источник деталей (api_fields / detail_url) в БД
        #    и сразу переходим к записи по данным УРОВНЯ СПИСКА, чтобы сбой API деталей
        #    площадки (напр. mos.ru 402) не блокировал скоринг и не валил проход.
        record: dict[str, Any] = {**list_vars}
        record["url"] = (
            detail_url
            if detail_url.startswith("http")
            else self._platform.url.rstrip("/") + detail_url
        )
        record["platform_id"] = self._platform_id

        # ИНН заказчика (ADR-4). Если ИНН отдаёт уже API списка (например mos.ru) —
        # сохраняем как есть. Остальные источники (API деталей, org-страница) — в
        # досборке деталей (ниже), чтобы не блокировать очередь на этом шаге.
        if list_vars.get("inn"):
            record["inn"] = list_vars["inn"]

        # Контекст досборки деталей (BR-08): api_fields для API-площадок (need_id и т.п.);
        # для DOM-площадок достаточно detail_url (уже в url) — ставим маркер, чтобы
        # find_scored_without_details знал, что досборка ещё не выполнена.
        if self._has_detail_source:
            record["detail_api"] = api_fields if api_fields is not None else {"source": "dom"}

        # Активна ли закупка (is_active): не активна, если задан неактивный статус
        # (не входит в active_statuses). Проверка срока актуальности (deadline)
        # выполняется на стороне клиента (репозиторий/API), а не при записи.
        record["is_active"] = self._is_active(record)

        # 8) JSONB-карточка на уровне списка (детали дособираются ниже, в досборке).
        record["detail_json"] = json_safe(record)

        # Клиентская фильтрация (R9) и запись — ВЕЕРОМ по профилям текущего обхода.
        # Для одиночного профиля ранний фильтр уже применён к subject из карточки;
        # для группы профилей фильтруем каждого по полной (уровень списка) записи.
        early_applied = bool(early_subject and not multi and ctxs)
        saved_any = False
        pushed_scoring: set[tuple[int, int]] = set()
        for ctx in ctxs:
            if not early_applied:
                if not keywords_match(record, ctx.keywords):
                    logger.info(
                        "Закупка %s отброшена: нет совпадений с ключевыми словами профиля",
                        number,
                    )
                    continue
                if exclusions_present(record, ctx.exclusion_words):
                    logger.info(
                        "Закупка %s отброшена: слова-исключения в описании",
                        number,
                    )
                    continue

            # Stop-условия по срокам (deadline).
            if self._check_stop_conditions(record):
                continue

            # 9) запись в БД + защита от дубликатов (закупка общая, evaluations — своя).
            saved = await self._persist(record)
            if saved:
                saved_any = True
                if self._known_numbers is not None:
                    self._known_numbers.add(str(number))

            # 9-бис) ключевые слова, по которым закупка отобрана профилем (R9):
            # они записываются в procurement_evaluations.matched_keywords ещё до
            # внешнего скоринга (оценка find-or-create обновляется стадиями каскада).
            # Записываем и для уже существующих закупок (saved=False) — важно в
            # мультипрофильном обходе: новый профиль оценивает общую закупку.
            if self._repository is not None and ctx is not None and record.get("id") is not None:
                hit = matched_keywords(record, ctx.keywords)
                if hit:
                    try:
                        await self._repository.record_matched_keywords(
                            int(record["id"]), ctx.profile.id, hit
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Не удалось записать matched_keywords закупки %s: %s",
                            record.get("number"),
                            exc,
                        )

                    # 10) авто-пуш задания на внешний скоринг (ADR-7) — ПО КАЖДОМУ профилю,
                    #     отобравшему закупку (пер-профильно, BR-07): результат стадии
                    #     засчитывается именно этому профилю. Дедупликация — по паре
                    #     (procurement_id, profile_id). Приоритет — время обновления/
                    #     публикации закупки (ZPOPMAX берёт больший score).
                    if self._transport is not None:
                        key = (int(record["id"]), ctx.profile.id)
                        if key not in pushed_scoring:
                            pushed_scoring.add(key)
                            ts = record.get("update_date") or record.get("publication_date")
                            priority = self._now.timestamp()
                            if isinstance(ts, datetime):
                                priority = ts.timestamp()
                            elif isinstance(ts, str):
                                with contextlib.suppress(ValueError):
                                    priority = datetime.fromisoformat(ts).timestamp()
                            try:
                                await self._transport.enqueue(
                                    int(record["id"]), priority, profile_id=ctx.profile.id
                                )
                                # Метка успешной постановки по паре (закупка, профиль)
                                # (recovery догоняет, не попавшие в очередь).
                                await self._repository.mark_scoring_queued(
                                    int(record["id"]), ctx.profile.id, self._now
                                )
                                logger.info(
                                    "Закупка %s поставлена в очередь скоринга (профиль %s)",
                                    record.get("number"),
                                    ctx.profile.id,
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "Не удалось поставить задание на скоринг закупки %s "
                                    "(профиль %s): %s",
                                    record.get("number"),
                                    ctx.profile.id,
                                    exc,
                                )

        return False, number, saved_any

    @property
    def _has_detail_source(self) -> bool:
        """Есть ли у площадки источник деталей для досборки (API или DOM).

        API-площадки (``detail.api_format``) отдают детали по JSON; DOM-площадки —
        переходят на детальную страницу и извлекают ``detail.variables``/файлы.
        Если деталей нет вовсе — досборка не нужна (закупка остаётся на уровне списка).
        """
        d = self._platform.detail
        return bool(d.api_format or d.variables or d.files or d.additional_pages)

    async def _collect_pending_details(self, page: Page) -> None:
        """Досборка деталей площадки best-effort ПОСЛЕ получения результата скоринга.

        BR-08: детали дособираются ТОЛЬКО для закупок, по которым парсер уже получил
        результат скоринга (``procurement_evaluations.fit_score IS NOT NULL`` — внешний
        сервис вернул результат через POST /score). Проход идёт по БД
        (``find_scored_without_details``), а не по только что сохранённым записям:
        новые закупки в этом же цикле скоринг ещё не получали, поэтому досборка
        происходит на следующих проходах планировщика. Любой сбой (в т.ч. HTTP 402 от
        API деталей) НЕ роняет проход: карточка остаётся на уровне списка, досборка
        повторится в следующем цикле.
        """
        if self._repository is None or not self._has_detail_source:
            return
        items = await self._repository.find_scored_without_details(
            self._platform_id, limit=self._cfg.parser.details_batch
        )
        if not items:
            return
        logger.info(
            "Площадка %s: досборка деталей для %d закупок ПОСЛЕ скоринга (best-effort)",
            self._platform_id,
            len(items),
        )
        for item in items:
            number = item["number"]
            try:
                list_vars = {"number": number}
                detail_vars, files, api_inn, customer_link = await self._fetch_record_details(
                    page,
                    list_vars,
                    item["url"],
                    item["detail_api"],
                    number,
                )
                record = dict(item["detail_json"] or {})
                # Не затираем значения уровня списка значением None (например, НМЦК,
                # если детальная SPA не успела отрисовать поле) — как в основном пути.
                record.update({k: v for k, v in detail_vars.items() if v is not None})
                if files:
                    record["files_json"] = files
                if api_inn and not record.get("inn"):
                    record["inn"] = api_inn
                # ИНН с org-страницы (DOM-площадки): только если список/API его не дали.
                if customer_link and not record.get("inn"):
                    record["inn"] = await self._resolve_customer_inn(page, customer_link)
                record["is_active"] = self._is_active(record)
                record["detail_json"] = json_safe(record)
                await self._repository.update_details(int(item["id"]), record)
                logger.info(
                    "Площадка %s: догружены детали закупки %s",
                    self._platform_id,
                    number,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Площадка %s: детали закупки %s не догружены (карточка остаётся "
                    "на уровне списка): %s",
                    self._platform_id,
                    number,
                    exc,
                )

    async def _fetch_record_details(
        self,
        page: Page,
        list_vars: dict[str, Any],
        detail_url: str | None,
        api_fields: dict[str, Any] | None,
        number: Any,
    ) -> tuple[dict[str, Any], list[dict[str, str]], str | None, str | None]:
        """Извлекает детали закупки (API или DOM) для досборки (BR-08).

        Повторяет прежний блок «3) детали»: API-площадки — ``fetch_api_details``,
        DOM-площадки — переход на детальную страницу в отдельной вкладке + доп.
        страницы и файловая страница. Возвращает
        ``(detail_vars, files, api_inn, customer_link)``.
        """
        files: list[dict[str, str]] = []
        api_inn: str | None = None
        customer_link: str | None = None
        detail_page: Page | None = None
        close_detail = False
        retry_cfg = self._cfg.parser.retry
        try:
            if self._platform.detail.api_format:
                detail_vars, files, api_inn = await run_with_retry(
                    partial(fetch_api_details, page, self._platform, list_vars, api_fields),
                    retry=retry_cfg,
                    circuit=self._site_cb,
                    label=f"Детали {number}",
                )
                return detail_vars, files, api_inn, None

            if self._new_page is not None:
                detail_page = await self._new_page()
                close_detail = True
            else:
                detail_page = page
            if not detail_url:
                logger.debug("Детали %s: нет ссылки на детальную страницу", number)
                return {}, [], None, None
            await run_with_retry(
                lambda: open_detail(detail_page, detail_url, self._platform),
                retry=retry_cfg,
                circuit=self._site_cb,
                label=f"Детали {number}",
            )
            detail_vars = await extract_detail_vars(detail_page, self._platform)
            customer_link = await capture_customer_link(detail_page, self._platform)
            # Доп. страницы деталей (например, ОКПД2 223-ФЗ на lot-list).
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
                    detail_vars.update({k: v for k, v in extra.items() if v is not None})
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Доп. страница деталей не обработана: %s", exc)
            # Файловая страница (например, ЕИС documents.html).
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
            return detail_vars, files, api_inn, customer_link
        finally:
            if close_detail and detail_page is not None:
                await detail_page.close()
