"""Обработка одной записи списка (DOM/API): фильтр, запись, скоринг.

Выделено из прежнего ``parser/orchestrator/orchestrator.py``: метод
``_process_list_record`` класса Orchestrator перенесён в миксин
``RecordProcessingMixin`` без изменения логики. С BR-08 платформенные детали
(ОКПД2/файлы/ИНН) не запрашиваются до скоринга: запись идёт по данным уровня
списка, а детали догружаются в обработчике ``POST /score`` ПОСЛЕ получения
результата скоринга, ПЕРЕД записью скора в БД (сбой деталей не роняет проход).
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from typing import Any

from playwright.async_api import Page

from zakupki_parser.parser.filtering import (
    exclusions_present,
    keywords_match,
    matched_keywords,
)
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.parser.orchestrator.state import OrchestratorState

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

        # 3) детали ПЕРЕНЕСЕНЫ в обработчик POST /score (BR-08): детали площадки
        #    догружаются ПОСЛЕ получения результата скоринга, ПЕРЕД записью скора в БД.
        #    Здесь фиксируем в БД контекст запроса деталей (api_fields: need_id и т.п.),
        #    чтобы set_score мог повторить запрос без переоткрытия детальной страницы,
        #    и сразу переходим к записи по данным УРОВНЯ СПИСКА, чтобы сбой API деталей
        #    (напр. mos.ru 402) не блокировал скоринг и не валил проход.
        record: dict[str, Any] = {**list_vars}
        record["url"] = (
            detail_url
            if detail_url.startswith("http")
            else self._platform.url.rstrip("/") + detail_url
        )
        record["platform_id"] = self._platform_id

        # ИНН заказчика (ADR-4). Если ИНН отдаёт уже API списка (например mos.ru) —
        # сохраняем как есть. Прочие источники (API деталей) — в досборке в set_score.
        if list_vars.get("inn"):
            record["inn"] = list_vars["inn"]

        # Контекст досборки деталей (BR-08): api_fields для API-площадок (need_id
        # и т.п.), которые понадобятся в обработчике POST /score для запроса деталей.
        if api_fields is not None:
            record["detail_api"] = api_fields

        # Активна ли закупка (is_active): не активна, если задан неактивный статус
        # (не входит в active_statuses). Проверка срока актуальности (deadline)
        # выполняется на стороне клиента (репозиторий/API), а не при записи.
        record["is_active"] = self._is_active(record)

        # 8) JSONB-карточка на уровне списка (детали дособираются в set_score).
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
