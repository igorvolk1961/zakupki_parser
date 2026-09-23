"""RAG-пайплайн анализа по персональным вопросам профиля.

Персонализированные вопросы профиля (единственное сохраняемое RAG-звено) обрабатываются
по одному LLM-вызову на вопрос: эмбеддинги вопросов кэшируются, контекст — разделы ВСЕХ
документов закупки (не только файла, определённого как ТЗ, — требования к участнику
часто лежат в других приложениях, см. ``RagAnalyzer._collect_document_chunks``).
Обязательные стоп-условия ушли в отдельный детерминированный поиск
«Требований к участнику» по всем документам плюс LLM-заполнение ``data``
(``fill_requirements_data``). Результат — ``rag_report`` для карточки.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from analysis_service.llm import LlmClient
from analysis_service.pipeline.chunker import split_tz_sections
from analysis_service.pipeline.matcher import MARKERS, SEVERITY
from analysis_service.pipeline.prompts import (
    build_requirements_data_messages,
    build_verdict_messages,
)
from analysis_service.pipeline.report_fields import ReportFieldExtractor
from analysis_service.settings import Settings
from scoring_common.costing import stage_metrics_with_components
from scoring_common.embeddings import Embeddable, cosine_similarity
from scoring_common.langfuse import parent_span, trace_url_from_trace_id
from scoring_common.requirements import enumerate_document_refs
from scoring_common.tz import clean_text, extract_text_cached, resolve_tz_content

logger = logging.getLogger(__name__)

VERDICT_NONE: Literal["no_stop_condition"] = "no_stop_condition"
VERDICT_SOFT: Literal["soft"] = "soft"
VERDICT_ABSOLUTE: Literal["absolute"] = "absolute"
VERDICT_UNAVAILABLE: Literal["unavailable"] = "unavailable"
VERDICTS = (VERDICT_NONE, VERDICT_SOFT, VERDICT_ABSOLUTE, VERDICT_UNAVAILABLE)
Verdict = Literal["no_stop_condition", "absolute", "soft", "unavailable"]

# Ключи полей структуры «Требования к участнику»: каждый тип — список объектов.
_REQUIREMENT_KEYS = ("licenses", "experience", "minprom", "other")


def _requirement_items(value: Any) -> list[Any]:
    """Элементы поля требований: список; легаси-форма (один объект) приводится к списку."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


class QuestionVerdict(BaseModel):
    """Вердикт по одному профильному вопросу."""

    question_id: str
    question_text: str
    verdict: Literal["no_stop_condition", "absolute", "soft", "unavailable"]
    severity: int = Field(ge=0, le=2)
    marker: str = Field(default="", description="🔴/🟡/🟢/⚪ для карточки")
    excerpt: str | None = Field(default=None, description="цитата фрагмента ТЗ")
    reasoning: str = Field(default="", description="краткое обоснование")
    source: Literal["system", "profile"] = Field(
        default="profile", description="источник вопроса: системный или из профиля"
    )
    question_version: str | None = Field(
        default=None, description="версия набора системных вопросов"
    )
    facts: dict[str, Any] = Field(
        default_factory=dict, description="факты, извлечённые из ТЗ (системные вопросы)"
    )


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
        # Кэш эмбеддингов пользовательских вопросов (вопросы профиля одинаковы
        # для всех закупок). Системные вопросы эмбеддингов не требуют вовсе.
        self._question_embedding_cache: dict[str, list[float]] = {}
        # Конструктор отчётных полей (FR-12.2) — переиспользует уже посчитанные
        # чанки/эмбеддинги документов закупки, см. _analyze().
        self._field_extractor = ReportFieldExtractor(settings, embedder, llm)

    async def analyze(
        self,
        record: dict[str, Any],
        questions: list[dict[str, Any]],
        report_fields: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """RAG-отчёт по персональным вопросам профиля. best-effort.

        Весь прогон (эмбеддинги, LLM-вердикты) вкладывается в единый родительский
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
            report = await self._analyze(record, questions, report_fields or [], generated_at)
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
        questions: list[dict[str, Any]],
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
                "questions": [],
                "fields": [],
                "generated_at": generated_at,
                "status": "no_tz",
            }

        verdicts: list[dict[str, Any]] = []

        chunk_vectors = await self._embedder.embed(chunks)
        if chunk_vectors is None or len(chunk_vectors) != len(chunks):
            # Векторы недоступны: вопросы профиля оценить нельзя (best-effort).
            embed_error = (
                "Не удалось вычислить эмбеддинги чанков документов (вопросы профиля не оценены)"
            )
            for question in questions:
                question_id = str(question.get("id") or "")
                question_text = str(question.get("text") or "").strip()
                if question_id and question_text:
                    verdicts.append(
                        self._profile_verdict(
                            question_id, question_text, VERDICT_UNAVAILABLE, embed_error, None
                        )
                    )
            return {
                "tz_found": ref is not None,
                "tz_file": tz_file,
                "questions": verdicts,
                "fields": [],
                "generated_at": generated_at,
                "error": embed_error,
                "status": "deferred",
            }

        for question in questions:
            question_id = str(question.get("id") or "")
            question_text = str(question.get("text") or "").strip()
            if not question_id or not question_text:
                continue
            verdicts.append(
                await self._verdict_for_question(
                    question_id, question_text, chunks, chunk_vectors, chunk_sources
                )
            )

        field_values = await self._field_extractor.extract(
            report_fields, chunks, chunk_vectors, chunk_sources
        )

        return {
            "tz_found": ref is not None,
            "tz_file": tz_file,
            "questions": verdicts,
            "fields": field_values,
            "generated_at": generated_at,
            "status": self._status(True, None, verdicts),
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
    def _status(
        tz_found: bool, error: str | None, questions: list[dict[str, Any]]
    ) -> Literal["no_tz", "deferred", "error", "ok"]:
        """Итоговый статус RAG-отчёта: ок / отложен / ошибка / ТЗ не найдено."""
        if not tz_found:
            return "no_tz"
        if any(q.get("verdict") == VERDICT_UNAVAILABLE for q in questions):
            return "deferred"
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
        достраиваются. Легаси-форма (dict вместо списка) приводится к списку.
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

    # ------------------------------------------------------------------ #
    # Пользовательские вопросы профиля (по одному LLM-вызову на вопрос)
    # ------------------------------------------------------------------ #
    async def _verdict_for_question(
        self,
        question_id: str,
        question_text: str,
        chunks: list[str],
        chunk_vectors: list[list[float]],
        chunk_sources: list[str],
    ) -> dict[str, Any]:
        """Вердикт по одному вопросу профиля (best-effort: сбой → unavailable).

        Топ-k чанков ищется по ВСЕМ документам закупки разом (не по одному
        файлу) — соседние по индексу ``chunks``/``chunk_sources`` могут быть из
        разных документов. Источник каждого чанка помечается в контексте LLM
        (``[Источник: <файл>]``), чтобы модель могла корректно на него сослаться.
        """
        q_vector = self._question_embedding_cache.get(question_id)
        if q_vector is None:
            q_vector = await self._embedder.embed_one(question_text)
            if q_vector is None:
                return self._profile_verdict(
                    question_id,
                    question_text,
                    VERDICT_UNAVAILABLE,
                    "Не удалось вычислить эмбеддинг вопроса (анализ пропущен)",
                    None,
                )
            self._question_embedding_cache[question_id] = q_vector

        scored = sorted(
            ((cosine_similarity(q_vector, cv), idx) for idx, cv in enumerate(chunk_vectors)),
            reverse=True,
        )
        top_idx = [idx for _, idx in scored[: self._settings.top_k]]
        context = "\n\n---\n\n".join(
            f"[Источник: {chunk_sources[idx]}]\n{chunks[idx]}" for idx in top_idx
        )

        system, user = build_verdict_messages(question_text, context)
        data = await self._llm.chat_json(system, user)
        if data is None:
            return self._profile_verdict(
                question_id,
                question_text,
                VERDICT_UNAVAILABLE,
                "LLM-верификация не выполнена (сбой)",
                None,
            )

        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            verdict = VERDICT_NONE
        return self._profile_verdict(
            question_id,
            question_text,
            cast(Verdict, verdict),
            str(data.get("reasoning") or ""),
            str(data.get("excerpt") or "")[:500] or None,
        )

    def _profile_verdict(
        self,
        question_id: str,
        question_text: str,
        verdict: Verdict,
        reasoning: str,
        excerpt: str | None,
    ) -> dict[str, Any]:
        return QuestionVerdict(
            question_id=question_id,
            question_text=question_text,
            verdict=verdict,
            severity=SEVERITY[verdict],
            marker=MARKERS[verdict],
            excerpt=excerpt,
            reasoning=reasoning,
            source="profile",
        ).model_dump()
