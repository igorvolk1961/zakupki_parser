"""RAG-пайплайн анализа: отчётные поля профиля по документам закупки.

Отчётные поля (``analysis_service.pipeline.report_fields``) извлекаются по
одному LLM-вызову на поле; контекст — разделы ВСЕХ документов закупки (не
только файла, определённого как ТЗ, — требования к участнику часто лежат в
других приложениях, см. ``RagAnalyzer._collect_document_chunks``). Обязательные
стоп-условия ушли в отдельный детерминированный поиск «Требований к участнику»
по всем документам плюс LLM-заполнение ``data`` (``fill_requirements_data``).
Результат — ``rag_report`` для карточки.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal

from analysis_service.llm import LlmClient
from analysis_service.pipeline.chunker import split_tz_sections
from analysis_service.pipeline.prompts import build_requirements_data_messages
from analysis_service.pipeline.report_fields import ReportFieldExtractor
from analysis_service.settings import Settings
from scoring_common.costing import stage_metrics_with_components
from scoring_common.embeddings import Embeddable
from scoring_common.langfuse import parent_span, trace_url_from_trace_id
from scoring_common.requirements import enumerate_document_refs
from scoring_common.tz import clean_text, extract_text_cached, resolve_tz_content

logger = logging.getLogger(__name__)

# Ключи полей структуры «Требования к участнику»: каждый тип — список объектов.
_REQUIREMENT_KEYS = ("licenses", "experience", "minprom", "other")


def _requirement_items(value: Any) -> list[Any]:
    """Элементы поля требований (список); иное значение — пусто."""
    return value if isinstance(value, list) else []


class RagAnalyzer:
    """Выполняет RAG-анализ: документы карточки → чанки → вердикты по вопросам."""

    def __init__(
        self,
        settings: Settings,
        embedder: Embeddable,
        llm: LlmClient,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        self._llm = llm
        # Конструктор отчётных полей (FR-12.2) — переиспользует уже посчитанные
        # чанки/эмбеддинги документов закупки, см. _analyze().
        self._field_extractor = ReportFieldExtractor(settings, embedder, llm)

    async def analyze(
        self,
        record: dict[str, Any],
        report_fields: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """RAG-отчёт по отчётным полям профиля. best-effort.

        Весь прогон (эмбеддинги, LLM-вызовы) вкладывается в единый родительский
        span LangFuse ``rag_analysis``: трейсы эмбеддингов становятся дочерними
        спанами с общим родителем вместо отдельных корневых наблюдений.
        """
        generated_at = datetime.now(UTC).isoformat()
        run_metadata = {"generated_at": generated_at}
        if metadata:
            run_metadata.update(metadata)
        stage_start = time.perf_counter()
        with parent_span("rag_analysis", metadata=run_metadata) as parent:
            trace_id = getattr(parent, "trace_id", None)
            # Стоимость LLM- и эмбеддинг-вызовов именно этого прогона: сбрасываем
            # счётчики ДО анализа и читаем ПОСЛЕ, чтобы в отчёт попала цена этой
            # закупки (клиенты переиспользуются воркером на всех закупках).
            self._llm.reset_cost()
            getattr(self._embedder, "reset_cost", lambda: None)()
            getattr(self._embedder, "reset_metrics", lambda: None)()
            report = await self._analyze(record, report_fields or [], generated_at)
        duration_ms = (time.perf_counter() - stage_start) * 1000.0
        llm_metrics: dict[str, Any] = getattr(self._llm, "metrics", lambda: {})()
        emb_metrics: dict[str, Any] = getattr(self._embedder, "metrics", lambda: {})()
        report["cost"] = self._stage_cost_metrics(duration_ms, llm_metrics, emb_metrics)
        report["trace_url"] = trace_url_from_trace_id(trace_id)
        return report

    def _stage_cost_metrics(
        self,
        duration_ms: float,
        llm_metrics: dict[str, Any],
        emb_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Метрики стадии анализа (LLM + эмбеддинги) для карточки закупки.

        ``usd`` берётся из авторитетного источника (``total_cost_usd`` + стоимость
        эмбеддингов), а разбивка токенов/стоимости/латенси — из накопленных клиентами
        агрегатов (в ``components`` LLM и эмбеддинги хранятся раздельно). Для фолбэка
        на старые/заглушечные клиенты без ``metrics`` разбивка остаётся пустой,
        общая стоимость — корректной.
        """
        total_usd = self._llm.total_cost_usd + float(getattr(self._embedder, "cost_usd", 0.0))
        return stage_metrics_with_components(
            usd=total_usd,
            duration_ms=duration_ms,
            parts=[("llm", llm_metrics), ("embeddings", emb_metrics)],
        )

    async def _analyze(
        self,
        record: dict[str, Any],
        report_fields: list[dict[str, Any]],
        generated_at: str,
    ) -> dict[str, Any]:
        # «ТЗ»/«Описание» — только понятное имя файла для карточки (та же эвристика
        # «нет обязанностей Исполнителя → взять Описание», что и раньше); НЕ сужает
        # область поиска ответов — она теперь по всем документам закупки ниже.
        try:
            ref, _unused_text = await asyncio.to_thread(
                resolve_tz_content,
                record,
                self._settings.tz_download_timeout,
                self._settings.tz_verify_ssl,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, не критично для tz_file
            logger.warning("Не удалось определить файл ТЗ закупки: %s", exc)
            ref = None
        tz_file = ref.name if ref is not None else None

        chunks, chunk_sources = await self._collect_document_chunks(record)
        if not chunks:
            return {
                "tz_found": ref is not None,
                "tz_file": tz_file,
                "fields": [],
                "generated_at": generated_at,
                "status": "no_tz",
            }

        chunk_vectors = await self._embedder.embed(chunks)
        if chunk_vectors is None or len(chunk_vectors) != len(chunks):
            # Векторы недоступны: отчётные поля оценить нельзя (best-effort).
            embed_error = "Не удалось вычислить эмбеддинги чанков документов (поля не оценены)"
            return {
                "tz_found": ref is not None,
                "tz_file": tz_file,
                "fields": [],
                "generated_at": generated_at,
                "error": embed_error,
                "status": "deferred",
            }

        field_values = await self._field_extractor.extract(
            report_fields, chunks, chunk_vectors, chunk_sources
        )

        return {
            "tz_found": ref is not None,
            "tz_file": tz_file,
            "fields": field_values,
            "generated_at": generated_at,
            "status": self._status(True, None),
        }

    async def _collect_document_chunks(self, record: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Чанки со ВСЕХ документов закупки (не только файла ТЗ), с указанием источника.

        Требования к участнику часто лежат не в ТЗ, а в проекте контракта,
        извещении или другом приложении — тот же охват документов, что уже
        использует детерминированный поиск требований (``enumerate_document_refs``:
        архивы разворачиваются, каждый файл — независимо, сбой одного не
        останавливает остальные). ``chunk_sources`` — имя документа-источника
        для каждого чанка (тот же индекс, что и в ``chunks``) — используется,
        чтобы указать LLM и итоговому отчёту, из какого файла взят фрагмент.
        Лимиты (``max_files_per_procurement``/``max_document_chars``) — те же,
        что у фоновой индексации, чтобы закупка с большим числом крупных
        вложений не раздувала стоимость эмбеддингов на один анализ.
        """
        chunks: list[str] = []
        sources: list[str] = []
        try:
            refs = await asyncio.to_thread(
                enumerate_document_refs,
                record,
                self._settings.tz_download_timeout,
                self._settings.tz_verify_ssl,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, закупка не теряется
            logger.warning("Не удалось перечислить документы закупки: %s", exc)
            return chunks, sources

        total_chars = 0
        for ref in refs[: self._settings.max_files_per_procurement]:
            try:
                raw = await asyncio.to_thread(
                    extract_text_cached,
                    ref,
                    self._settings.tz_download_timeout,
                    verify_ssl=self._settings.tz_verify_ssl,
                )
            except Exception as exc:  # noqa: BLE001 — сбой одного файла не роняет остальные
                logger.warning("Не удалось извлечь текст документа %s: %s", ref.name, exc)
                continue
            text = clean_text(raw) if raw else ""
            if not text:
                continue
            doc_name = ref.name.rsplit("/", 1)[-1]
            for chunk in split_tz_sections(text, max_chars=self._settings.chunk_max_chars):
                chunks.append(chunk)
                sources.append(doc_name)
                total_chars += len(chunk)
            if total_chars >= self._settings.max_document_chars:
                break
        return chunks, sources

    @staticmethod
    def _status(tz_found: bool, error: str | None) -> Literal["no_tz", "error", "ok"]:
        """Итоговый статус RAG-отчёта: ок / ошибка / ТЗ не найдено.

        ``deferred`` (эмбеддинги недоступны) выставляется отдельным ранним
        возвратом в ``_analyze`` — сюда управление в этом случае не доходит.
        """
        if not tz_found:
            return "no_tz"
        if error:
            return "error"
        return "ok"

    # ------------------------------------------------------------------ #
    # Заполнение data структуры «Требования к участнику» (LLM-этап)
    # ------------------------------------------------------------------ #
    async def fill_requirements_data(self, structure: dict[str, Any]) -> dict[str, Any]:
        """LLM-заполнение ``data`` элементов структуры требований (per-procurement).

        В каждом поле (``licenses``/``experience``/``minprom``/``other``) каждый
        элемент ``{text, data, file_name}`` обрабатывается отдельным LLM-вызовом по
        своей JSON-схеме. Уже заполненные ``data`` не пересчитываются (идемпотентность).
        При сбое вызова ``data`` остаётся ``None`` (best-effort), остальные поля
        достраиваются.
        """
        filled: dict[str, Any] = {}
        for key in _REQUIREMENT_KEYS:
            entries = _requirement_items(structure.get(key))
            if not entries:
                continue
            filled_items: list[Any] = []
            for item in entries:
                if not isinstance(item, dict):
                    filled_items.append(item)
                    continue
                text = item.get("text") or ""
                if not text or item.get("data") is not None:
                    filled_items.append(item)
                    continue
                data = await self._llm_requirement_data(key, text)
                # сохранить служебные поля (file_name, additional, negated, universal).
                rebuilt: dict[str, Any] = dict(item)
                rebuilt["text"] = text
                rebuilt["data"] = data
                filled_items.append(rebuilt)
            filled[key] = filled_items
        # Служебные ключи (не разделы требований) переносим как есть.
        for key, value in structure.items():
            if key not in _REQUIREMENT_KEYS:
                filled[key] = value
        return filled

    async def _llm_requirement_data(self, kind: str, text: str) -> dict[str, Any] | None:
        """JSON-структура требования вида ``kind`` из текста раздела (или None при сбое)."""
        system, user = build_requirements_data_messages(kind, text)
        data = await self._llm.chat_json(system, user)
        return data if isinstance(data, dict) else None
