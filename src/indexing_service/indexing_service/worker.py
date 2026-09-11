"""Фоновый воркер стадии Index: потребляет задачи из Redis-очереди.

Цикл: ``ZPOPMAX index:jobs`` -> получить карточку закупки (``files_json``) из
парсера через REST -> скачать+извлечь текст документов (``scoring_common.tz``,
тот же модуль, что и ``analysis_service``/просмотр ТЗ в карточке) -> ``LPUSH
index:results``. Retry/recovery-логика по недоступности парсера — общая
(``scoring_common.stage_worker``); ошибки скачивания/извлечения ОТДЕЛЬНЫХ
документов не роняют задачу — результат публикуется частично (``document_text``
из тех файлов, что удалось обработать); задача полностью отмечается
``status=error`` только если не удалось обработать НИ ОДНОГО файла.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from indexing_service.settings import Settings
from scoring_common.parser_api import ParserApiClient
from scoring_common.queue import StageQueue
from scoring_common.stage_worker import process_stage_job
from scoring_common.tz import extract_text_cached
from scoring_common.tz.files import collect_files

logger = logging.getLogger(__name__)


class IndexWorker:
    """Воркер обработки задач стадии Index (фоновая индексация по ОКПД2)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue = StageQueue(settings)
        self._parser = ParserApiClient(
            settings.parser_api_url, internal_token=settings.parser_internal_token
        )
        # Своя «вежливость»: дозагрузка файлов сегодня нигде не ограничена
        # Delayer'ом (см. риск №3 плана индексации) — собственный лимит.
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)

    async def run_forever(self) -> None:
        await self._queue.connect()
        logger.info("Index worker started (poll %.1fs)", self._settings.queue_poll_seconds)
        try:
            while True:
                await self._queue.recover_stale()
                await self._process_once()
                await asyncio.sleep(self._settings.queue_poll_seconds)
        finally:
            await self._queue.close()

    async def _process_once(self) -> None:
        job = await self._queue.pop_job()
        if job is None:
            return
        procurement_id, profile_id, priority = job
        logger.info("Processing Index for procurement %s (priority=%.2f)", procurement_id, priority)
        await process_stage_job(
            self._queue,
            self._parser,
            procurement_id,
            profile_id,
            priority,
            retry_backoff_seconds=self._settings.parser_retry_backoff_seconds,
            compute=self._compute_payload,
        )

    async def _compute_payload(
        self, record: dict[str, Any], procurement_id: int, profile_id: int
    ) -> dict[str, Any]:
        """Скачать+извлечь текст документов закупки; payload — результат для ``index:results``.

        Не бросает исключений на ошибках скачивания/извлечения (это не повод
        возвращать задачу в очередь через ``process_stage_job`` — файл битый/недоступен
        сейчас, не транспортная проблема парсера) — они отражаются полями
        ``status``/``error_message`` результата.
        """
        base: dict[str, Any] = {"procurement_id": procurement_id, "stage": "index"}
        files = record.get("files_json") or []
        if not files:
            return {**base, "status": "indexed", "document_text": ""}
        try:
            refs = collect_files({"files_json": files})
            refs = refs[: self._settings.max_files_per_procurement]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось разобрать files_json закупки %s: %s", procurement_id, exc)
            return {**base, "status": "error", "error_message": str(exc)[:2000]}

        texts: list[str] = []
        errors: list[str] = []
        total_chars = 0
        async with self._semaphore:
            for i, ref in enumerate(refs):
                if i > 0:
                    await asyncio.sleep(self._settings.download_delay_seconds)
                try:
                    text = await asyncio.to_thread(
                        extract_text_cached,
                        ref,
                        self._settings.download_timeout_seconds,
                        verify_ssl=self._settings.verify_ssl,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{ref.name}: {exc}")
                    continue
                if not text:
                    continue
                texts.append(text)
                total_chars += len(text)
                if total_chars >= self._settings.max_document_chars:
                    break

        if errors and not texts:
            # Ни один документ не удалось обработать — закупка остаётся в индексе
            # (пустой текст), но со статусом error, чтобы её увидеть/повторить.
            return {
                **base,
                "status": "error",
                "error_message": "; ".join(errors)[:2000],
            }

        document_text = "\n".join(texts)
        return {
            **base,
            "status": "indexed",
            "document_text": document_text,
            "content_hash": hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
        }


async def run_worker(settings: Settings) -> None:
    worker = IndexWorker(settings)
    await worker.run_forever()
