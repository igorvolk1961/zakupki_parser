"""Обработка одной записи списка (DOM/API): фильтр, запись, скоринг.

Выделено из прежнего ``parser/orchestrator/orchestrator.py``: метод
``_process_list_record`` класса Orchestrator перенесён в миксин
``RecordProcessingMixin`` без изменения логики. С BR-08 платформенные детали
(ОКПД2/файлы/ИНН) не запрашиваются до скоринга: запись идёт по данным уровня
списка, а детали догружаются в обработчике ``POST /score`` ПОСЛЕ получения
результата скоринга, ПЕРЕД записью скора в БД (сбой деталей не роняет проход).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import Page

from scoring_common.tz import extract_text_cached
from scoring_common.tz.files import collect_files
from zakupki_parser.parser.detail import extract_details
from zakupki_parser.parser.filtering import (
    exclusions_present,
    exclusions_present_text,
    keywords_match,
    keywords_match_text,
    matched_keywords_text,
    region_match,
    subject_of,
)
from zakupki_parser.parser.json_utils import json_safe
from zakupki_parser.parser.orchestrator.state import OrchestratorState

# Имя логгера сохранено прежним (категория модуля orchestrator).
logger = logging.getLogger("zakupki_parser.parser.orchestrator.orchestrator")

# Live-фоллбэк поиска по документам (Profile.search_in_documents, вне
# проиндексированного диапазона ОКПД2) намеренно нарушает BR-08: дозагружает
# детали площадки (files_json) для записей, не прошедших фильтр по subject.
# Ограничения — защита от неконтролируемого роста нагрузки на площадку и памяти
# на один патологический пакет документов (архив с сотнями файлов).
_LIVE_FALLBACK_MAX_FILES = 20
_LIVE_FALLBACK_MAX_CHARS = 200_000
_LIVE_FALLBACK_CONCURRENCY = 2
_live_fallback_semaphore = asyncio.Semaphore(_LIVE_FALLBACK_CONCURRENCY)


class RecordProcessingMixin(OrchestratorState):
    """Обработка одной записи из списка (детали, фильтр, запись, пуш в скоринг)."""

    @staticmethod
    def _record_priority(record: dict[str, Any], now: datetime | None = None) -> float:
        """Приоритет очереди — время обновления/публикации закупки (ZPOPMAX берёт большее)."""
        ts = record.get("update_date") or record.get("publication_date")
        if isinstance(ts, datetime):
            return ts.timestamp()
        if isinstance(ts, str):
            with contextlib.suppress(ValueError):
                return datetime.fromisoformat(ts).timestamp()
        return (now or datetime.now(UTC)).timestamp()

    async def _enqueue_index_job(
        self,
        page: Page,
        record: dict[str, Any],
        list_vars: dict[str, Any],
        detail_url: str | None,
        api_fields: dict[str, Any] | None,
    ) -> None:
        """Дособирает детали площадки и ставит задание фоновой индексации документов.

        Вызывается один раз на закупку — при первом сохранении синтетическим
        индексным профилем (IndexingConfig, §1 плана индексации). Файлы
        (``files_json``) нужны воркеру ``indexing_service``, который сам по себе
        НЕ водит браузер (архитектурная граница: Playwright — только внутри
        парсера, см. ``docs/external-service-contract.md`) — поэтому детали
        дособираются здесь же, с уже открытой страницей текущего обхода, а не
        отдельным запросом из indexing_service.
        """
        details = await self._fetch_platform_details(
            page, list_vars, detail_url, api_fields, context="Индексный профиль"
        )
        if details is not None and self._repository is not None:
            detail_vars, files, api_inn = details
            data = dict(record.get("detail_json") or {})
            data.update({k: v for k, v in detail_vars.items() if v is not None})
            if files:
                data["files_json"] = files
            if api_inn and not data.get("inn"):
                data["inn"] = api_inn
            data["detail_json"] = json_safe(data)
            try:
                await self._repository.update_details(int(record["id"]), data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Индексный профиль: не удалось сохранить детали закупки %s: %s",
                    record.get("number"),
                    exc,
                )
        if self._transport is None:
            return
        try:
            await self._transport.enqueue(
                int(record["id"]),
                self._record_priority(record, self._now),
                stage="index",
                profile_id=0,
            )
            logger.info(
                "Закупка %s поставлена в очередь фоновой индексации документов",
                record.get("number"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось поставить задание индексации закупки %s: %s",
                record.get("number"),
                exc,
            )

    async def _fetch_platform_details(
        self,
        page: Page,
        list_vars: dict[str, Any],
        detail_url: str | None,
        api_fields: dict[str, Any] | None,
        *,
        context: str,
    ) -> tuple[dict[str, Any], list[dict[str, str]], str | None] | None:
        """Дозагрузка деталей площадки (``extract_details`` — тот же общий интерфейс,
        что использует обработчик ``POST /score``, BR-08) с уже открытой страницей
        обхода листинга — переиспользуется live-фоллбэком поиска по документам
        (§1б плана индексации) и синтетическим индексным профилем (§1/1а). ``None``
        при сбое (не роняет обход — запись остаётся на уровне списка).
        """
        try:
            return await extract_details(page, self._platform, list_vars, detail_url, api_fields)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: не удалось дособрать детали площадки: %s", context, exc)
            return None

    async def _live_fallback_document_text(
        self,
        page: Page,
        list_vars: dict[str, Any],
        detail_url: str | None,
        api_fields: dict[str, Any] | None,
    ) -> str | None:
        """Текст документов закупки для live-фоллбэка (``Profile.search_in_documents``).

        Дозагружает детали площадки ради ``files_json`` (``_fetch_platform_details``),
        затем скачивает и извлекает текст вложений (``scoring_common.tz``, тот же
        модуль, что уже используют ``analysis_service`` и просмотр ТЗ в карточке
        закупки). Ограничено числом файлов/суммарной длиной текста
        (см. ``_LIVE_FALLBACK_*``) и семафором конкурентности — своя «вежливость»
        отдельно от обычного обхода листинга (``Delayer``), которой у дозагрузки
        деталей/файлов сегодня нет.
        """
        details = await self._fetch_platform_details(
            page, list_vars, detail_url, api_fields, context="Live-фоллбэк поиска в документах"
        )
        if details is None:
            return None
        _detail_vars, files, _inn = details
        if not files:
            return None
        refs = collect_files({"files_json": files})[:_LIVE_FALLBACK_MAX_FILES]
        if not refs:
            return None
        texts: list[str] = []
        total_chars = 0
        async with _live_fallback_semaphore:
            for ref in refs:
                text = await asyncio.to_thread(extract_text_cached, ref, 30.0)
                if not text:
                    continue
                texts.append(text)
                total_chars += len(text)
                if total_chars >= _LIVE_FALLBACK_MAX_CHARS:
                    break
        return "\n".join(texts) if texts else None

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
        # Номер закупки — ОБЯЗАТЕЛЬНЫЙ бизнес-ключ (nullable=False + unique) в пределах
        # площадки (number + platform_id). Номер всегда есть в карточке списка результатов
        # поиска; если он не извлёкся — это сбой селектора/поля API, а не штатная ситуация.
        # Никаких «запасных» источников не используем: фиксируем критическую ошибку и
        # НЕ пишем запись в БД (обход не роняем из-за одной битой карточки, но запись
        # попадает в «отсеяно» сводки received - saved - known).
        if number is None or str(number).strip() == "":
            logger.critical(
                "КРИТИЧНО: закупка без номера — запись невозможна "
                "(platform_id=%s, subject=%r, url=%s, поля_карточки=%s)",
                self._platform_id,
                list_vars.get("subject"),
                detail_url,
                list_vars,
            )
            return False, number, False

        if not detail_url:
            logger.debug("Нет ссылки на детали, пропуск (number=%s)", number)
            return False, number, False

        ctxs = self._profile_ctxs
        multi = len(ctxs) > 1
        early_subject = str(list_vars.get("subject") or "")
        # Ранняя клиентская фильтрация (R9) — только для одиночного профиля: subject
        # уже есть в карточке списка, применяем слова ДО запроса деталей, чтобы не
        # тратить лимиты API площадки на заведомо неподходящие закупки (mos.example 402).
        # Для мультипрофильного обхода ранний фильтр невозможен: запись нужна каждому
        # профилю, слова применяются после получения записи (цикл по ctxs ниже).
        early_applied = False
        if early_subject and not multi and ctxs:
            first = ctxs[0]
            if keywords_match(list_vars, first.keywords):
                if exclusions_present(list_vars, first.exclusion_words):
                    logger.info(
                        "Закупка %s отброшена: слова-исключения в описании",
                        number,
                    )
                    return False, number, False
                # Регион (клиентская пост-фильтрация, как R9) — ТОЛЬКО если регион уже
                # есть на уровне списка: для площадок, где регион дособирается с деталями
                # (BR-08), отброс до досборки деталей некорректен. Отсекаем по строковому
                # соответствию целевых регионов всегда, когда они заданы; дистанция
                # (max_region_distance_km) на сборе не применяется — она проверяется
                # только на этапе анализа (геокодер на сборе не вызывается).
                if (
                    first.target_regions
                    and list_vars.get("region")
                    and not region_match(list_vars, first.target_regions)
                ):
                    logger.info(
                        "Закупка %s отброшена: регион вне целевых профиля",
                        number,
                    )
                    return False, number, False
                early_applied = True
            elif not first.search_in_documents:
                logger.info(
                    "Закупка %s отброшена: нет совпадений с ключевыми словами профиля",
                    number,
                )
                return False, number, False
            # else: subject не совпал, но у (единственного) профиля включён поиск по
            # документам («искать ключевые слова в документах») — решение откладывается
            # до сборки полной записи и live-фоллбэка в цикле по ctxs ниже
            # (early_applied остаётся False).

        # 3) детали ПЕРЕНЕСЕНЫ в обработчик POST /score (BR-08): детали площадки
        #    догружаются ПОСЛЕ получения результата скоринга, ПЕРЕД записью скора в БД.
        #    Здесь фиксируем в БД контекст запроса деталей (api_fields: need_id и т.п.),
        #    чтобы set_score мог повторить запрос без переоткрытия детальной страницы,
        #    и сразу переходим к записи по данным УРОВНЯ СПИСКА, чтобы сбой API деталей
        #    (напр. mos.example 402) не блокировал скоринг и не валил проход.
        record: dict[str, Any] = {**list_vars}
        record["url"] = (
            detail_url
            if detail_url.startswith("http")
            else self._platform.url.rstrip("/") + detail_url
        )
        record["platform_id"] = self._platform_id

        # ИНН заказчика (ADR-4). Если ИНН отдаёт уже API списка (например mos.example) —
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
        saved_any = False
        pushed_scoring: set[tuple[int, int]] = set()
        # Текст документов закупки для live-фоллбэка — дозагружается ЛЕНИВО и не
        # более одного раза на запись (общий для всех ctx в веере), чтобы несколько
        # профилей с search_in_documents не плодили повторные обращения к площадке.
        document_text: str | None = None
        document_text_fetched = False
        for ctx in ctxs:
            match_text = subject_of(record)
            if not early_applied:
                matched = keywords_match_text(match_text, ctx.keywords)
                if not matched and ctx.search_in_documents:
                    if not document_text_fetched:
                        document_text = await self._live_fallback_document_text(
                            page, list_vars, detail_url, api_fields
                        )
                        document_text_fetched = True
                    if document_text:
                        match_text = (
                            f"{match_text}\n{document_text}" if match_text else document_text
                        )
                        matched = keywords_match_text(match_text, ctx.keywords)
                if not matched:
                    logger.info(
                        "Закупка %s отброшена: нет совпадений с ключевыми словами профиля",
                        number,
                    )
                    continue
                if exclusions_present_text(match_text, ctx.exclusion_words):
                    logger.info(
                        "Закупка %s отброшена: слова-исключения в описании",
                        number,
                    )
                    continue
                # Регион — клиентская пост-фильтрация (как ключевые слова R9), после
                # сборки полной записи. Отбрасываем только если регион известен уже на
                # уровне списка: неизвестный регион (досборка деталей BR-08) фильтром
                # не отсекается, чтобы не терять закупки до досборки деталей. Строковое
                # соответствие целевых регионов применяется всегда, когда они заданы;
                # дистанция (max_region_distance_km) — только на этапе анализа.
                if (
                    ctx.target_regions
                    and record.get("region")
                    and not region_match(record, ctx.target_regions)
                ):
                    logger.info(
                        "Закупка %s отброшена: регион вне целевых профиля",
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

            # 9-тер) синтетический индексный профиль (IndexingConfig, §1/1а плана
            # индексации): keywords всегда пусты, поэтому блок 9-бис/скоринга ниже
            # для него не выполняется ("if hit:" никогда не True). Вместо этого — при
            # первом сохранении закупки в этом диапазоне ОКПД2 — дособираем детали
            # площадки (files_json, нужен indexing_service) и ставим задание на
            # фоновую индексацию документов. Гейт на saved: закупка обрабатывается
            # инкрементальным обходом обычно один раз, повторный заход по уже
            # известной закупке не должен дублировать дозагрузку/постановку задания.
            if (
                saved
                and ctx.is_system_index
                and self._repository is not None
                and record.get("id") is not None
            ):
                await self._enqueue_index_job(page, record, list_vars, detail_url, api_fields)

            # 9-бис) ключевые слова, по которым закупка отобрана профилем (R9):
            # они записываются в procurement_evaluations.matched_keywords ещё до
            # внешнего скоринга (оценка find-or-create обновляется стадиями каскада).
            # Записываем и для уже существующих закупок (saved=False) — важно в
            # мультипрофильном обходе: новый профиль оценивает общую закупку.
            if self._repository is not None and ctx is not None and record.get("id") is not None:
                hit = matched_keywords_text(match_text, ctx.keywords)
                if hit:
                    # Хэш канонического содержания компетенций профиля (BR-07):
                    # ключ дедупликации скоринга — профили с идентичным содержанием
                    # компетенций обрабатываются один раз.
                    from zakupki_parser.storage.competencies import competencies_hash

                    comp_hash = competencies_hash(ctx.profile.competencies)
                    try:
                        await self._repository.record_matched_keywords(
                            int(record["id"]), ctx.profile.id, hit, comp_hash=comp_hash
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
                    #     BR-07 (дедупликация по содержанию компетенций): если для
                    #     этой закупки уже есть оценка-представитель группы с тем же
                    #     компетенциями (fit_score записан ИЛИ задание поставлено) —
                    #     НОВОЕ задание не ставится; профиль лишь подписывается под
                    #     результат группы (метка scoring_queued_at, результат придёт
                    #     через apply_score_to_comp_hash_group).
                    #     Мониторинг без скоринга: профили с scoring_allowed=False
                    #     (владельцу недоступна опция scoring) собираются, но задания
                    #     на LLM не ставятся — matched_keywords уже записаны выше.
                    if self._transport is not None and ctx.scoring_allowed:
                        key = (int(record["id"]), ctx.profile.id)
                        if key not in pushed_scoring:
                            pushed_scoring.add(key)
                            dedup = await self._repository.find_group_evaluation(
                                int(record["id"]), comp_hash
                            )
                            if dedup is not None:
                                await self._repository.mark_scoring_queued(
                                    int(record["id"]), ctx.profile.id, self._now
                                )
                                if dedup.fit_score is not None:
                                    # Группа уже скорирована: копируем результат
                                    # представителя текущему профилю (подписка).
                                    await self._repository.upsert_score(
                                        int(record["id"]),
                                        ctx.profile.id,
                                        score=dedup.score,
                                        fit_score=dedup.fit_score,
                                        p_win=dedup.p_win,
                                        margin=dedup.margin,
                                        score_method=dedup.score_method,
                                        embedding_similarity=dedup.embedding_similarity,
                                        langfuse_trace_url=dedup.langfuse_trace_url,
                                    )
                                    logger.info(
                                        "Закупка %s: применён результат группы компетенций "
                                        "(профиль %s)",
                                        record.get("number"),
                                        ctx.profile.id,
                                    )
                                else:
                                    # Группа ещё считается: подписываемся — результат
                                    # придёт через apply_score_to_comp_hash_group.
                                    logger.info(
                                        "Закупка %s: задание уже есть для группы "
                                        "компетенций (профиль %s подписан)",
                                        record.get("number"),
                                        ctx.profile.id,
                                    )
                                continue
                            priority = self._record_priority(record)
                            try:
                                await self._transport.enqueue(
                                    int(record["id"]), priority, profile_id=ctx.profile.id
                                )
                                # Метка успешной постановки по паре (закупка, профиль)
                                # (recovery догоняет, не попавшие в очередь).
                                await self._repository.mark_scoring_queued(
                                    int(record["id"]), ctx.profile.id, self._now
                                )
                                # Итерация цикла планировщика, в которую закупка
                                # поставлена в очередь — граница батча журнала «Метрики».
                                if self._iteration:
                                    await self._repository.mark_scoring_iteration(
                                        int(record["id"]), self._iteration
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
