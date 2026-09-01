"""RAG-пайплайн анализа по персональным вопросам профиля.

Персонализированные вопросы профиля (единственное сохраняемое RAG-звено) обрабатываются
по одному LLM-вызову на вопрос: эмбеддинги вопросов кэшируются, контекст — разделы ТЗ
(как раньше). Обязательные стоп-условия ушли в отдельный детерминированный поиск
«Требований к участнику» по всем документам плюс LLM-заполнение ``data``
(``fill_requirements_data``). Результат — ``rag_report`` для карточки.
"""

from __future__ import annotations

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
from analysis_service.settings import Settings
from scoring_common.costing import stage_metrics_with_components
from scoring_common.embeddings import Embeddable, cosine_similarity
from scoring_common.langfuse import parent_span, trace_url_from_trace_id
from scoring_common.tz import resolve_tz_content

logger = logging.getLogger(__name__)

VERDICT_NONE: Literal["no_stop_condition"] = "no_stop_condition"
VERDICT_SOFT: Literal["soft"] = "soft"
VERDICT_ABSOLUTE: Literal["absolute"] = "absolute"
VERDICT_UNAVAILABLE: Literal["unavailable"] = "unavailable"
VERDICTS = (VERDICT_NONE, VERDICT_SOFT, VERDICT_ABSOLUTE, VERDICT_UNAVAILABLE)
Verdict = Literal["no_stop_condition", "absolute", "soft", "unavailable"]

# Признак наличия требований к Исполнителю/Участнику/Подрядчику и фолбэк на
# документ «Описание» — общая логика в scoring_common.tz (resolve_tz_content),
# которая используется и анализом, и просмотром ТЗ с карточки.


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
    """Выполняет RAG-анализ: ТЗ карточки → чанки → вердикты по вопросам."""

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

    async def analyze(
        self,
        record: dict[str, Any],
        questions: list[dict[str, Any]],
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
            report = await self._analyze(record, questions, generated_at)
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
        generated_at: str,
    ) -> dict[str, Any]:
        ref, tz_text = resolve_tz_content(
            record,
            timeout=self._settings.tz_download_timeout,
            verify_ssl=self._settings.tz_verify_ssl,
        )
        tz_file = ref.name if ref is not None else None
        if ref is None:
            return {
                "tz_found": False,
                "tz_file": None,
                "questions": [],
                "generated_at": generated_at,
                "status": "no_tz",
            }
        if not tz_text:
            return {
                "tz_found": False,
                "tz_file": tz_file,
                "questions": [],
                "generated_at": generated_at,
                "status": "no_tz",
            }

        chunks = split_tz_sections(tz_text, max_chars=self._settings.chunk_max_chars)
        if not chunks:
            return {
                "tz_found": True,
                "tz_file": tz_file,
                "error": "Не удалось разбить текст ТЗ на чанки",
                "questions": [],
                "generated_at": generated_at,
                "status": "error",
            }

        verdicts: list[dict[str, Any]] = []

        chunk_vectors = await self._embedder.embed(chunks)
        if chunk_vectors is None or len(chunk_vectors) != len(chunks):
            # Векторы недоступны: вопросы профиля оценить нельзя (best-effort).
            embed_error = "Не удалось вычислить эмбеддинги чанков ТЗ (вопросы профиля не оценены)"
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
                "tz_found": True,
                "tz_file": tz_file,
                "questions": verdicts,
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
                await self._verdict_for_question(question_id, question_text, chunks, chunk_vectors)
            )

        return {
            "tz_found": True,
            "tz_file": tz_file,
            "questions": verdicts,
            "generated_at": generated_at,
            "status": self._status(True, None, verdicts),
        }

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
        """LLM-заполнение ``data`` каждого поля структуры требований (per-procurement).

        Три основных поля (``licenses``/``experience``/``minprom``) и каждый элемент
        ``other`` обрабатываются отдельным LLM-вызовом по своей JSON-схеме. Уже
        заполненные ``data`` не пересчитываются (идемпотентность). При сбое вызова
        ``data`` остаётся ``None`` (best-effort), остальные поля достраиваются.
        """
        filled: dict[str, Any] = {}
        for key in ("licenses", "experience", "minprom"):
            entry = structure.get(key)
            if not isinstance(entry, dict):
                continue
            text = entry.get("text") or ""
            if not text or entry.get("data") is not None:
                filled[key] = entry
                continue
            data = await self._llm_requirement_data(key, text)
            filled[key] = {"text": text, "data": data, "file_name": entry.get("file_name")}
        other = structure.get("other")
        if isinstance(other, list):
            other_filled: list[Any] = []
            for item in other:
                if not isinstance(item, dict):
                    other_filled.append(item)
                    continue
                text = item.get("text") or ""
                if not text or item.get("data") is not None:
                    other_filled.append(item)
                    continue
                data = await self._llm_requirement_data("other", text)
                other_filled.append(
                    {"text": text, "data": data, "file_name": item.get("file_name")}
                )
            filled["other"] = other_filled
        # Служебные ключи (не разделы требований) переносим как есть.
        for key, value in structure.items():
            if key not in ("licenses", "experience", "minprom", "other"):
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
    ) -> dict[str, Any]:
        """Вердикт по одному вопросу профиля (best-effort: сбой → unavailable)."""
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
        context = "\n\n---\n\n".join(chunks[idx] for idx in top_idx)

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
