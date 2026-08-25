"""RAG-пайплайн анализа стоп-условий по вопросам клиента.

Обязательные (системные) проверки — опыт (ПП РФ 2571), реестр Минпромторга,
лицензии/СРО — извлекаются из ТЗ одним LLM-вызовом (``batch_system``) по
лексически отобранным секциям, затем сопоставляются с фактами профиля
детерминированными правилами (``matcher``): профиль в промпт не попадает.
Пользовательские вопросы профиля обрабатываются по одному LLM-вызову на вопрос
(эмбеддинги вопросов кэшируются). Результат — ``rag_report`` для карточки.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from analysis_service.llm import LlmClient
from analysis_service.pipeline.chunker import split_tz_sections
from analysis_service.pipeline.matcher import (
    MARKERS,
    SEVERITY,
    apply_profile_facts,
)
from analysis_service.pipeline.prompts import (
    build_batch_system_messages,
    build_verdict_messages,
)
from analysis_service.pipeline.system_questions import (
    SYSTEM_QUESTIONS,
    SYSTEM_QUESTIONS_VERSION,
    SYSTEM_RETRIEVAL_PATTERNS,
)
from analysis_service.settings import Settings
from scoring_common.embeddings import EmbeddingClient, cosine_similarity
from scoring_common.tz import clean_text, extract_text, find_tz_reference

logger = logging.getLogger(__name__)

VERDICT_NONE: Literal["no_stop_condition"] = "no_stop_condition"
VERDICT_SOFT: Literal["soft"] = "soft"
VERDICT_ABSOLUTE: Literal["absolute"] = "absolute"
VERDICTS = (VERDICT_NONE, VERDICT_SOFT, VERDICT_ABSOLUTE)

# Ключи ответа batch_system.md → id системного вопроса.
_BATCH_KEYS: dict[str, str] = {
    "experience_2571": "sys:exp_2571",
    "minprom_registry": "sys:minprom_registry",
    "license_sro": "sys:license_sro",
}
_SYSTEM_TEXT: dict[str, str] = {q["id"]: q["text"] for q in SYSTEM_QUESTIONS}


class QuestionVerdict(BaseModel):
    """Вердикт по одному вопросу (системному или профильному)."""

    question_id: str
    question_text: str
    verdict: Literal["no_stop_condition", "absolute", "soft"]
    severity: int = Field(ge=0, le=2)
    marker: str = Field(default="", description="🔴/🟡/🟢 для карточки")
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
        embedder: EmbeddingClient,
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
        profile_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """RAG-отчёт: системные проверки + вопросы клиента. best-effort."""
        generated_at = datetime.now(UTC).isoformat()

        ref = find_tz_reference(record, timeout=self._settings.tz_download_timeout)
        tz_file = ref.name if ref is not None else None
        if ref is None:
            return {
                "tz_found": False,
                "tz_file": None,
                "questions": [],
                "generated_at": generated_at,
            }
        raw = extract_text(ref, timeout=self._settings.tz_download_timeout)
        tz_text = clean_text(raw) if raw else ""
        if not tz_text:
            return {
                "tz_found": False,
                "tz_file": tz_file,
                "questions": [],
                "generated_at": generated_at,
            }

        chunks = split_tz_sections(tz_text, max_chars=self._settings.chunk_max_chars)
        if not chunks:
            return {
                "tz_found": True,
                "tz_file": tz_file,
                "error": "Не удалось разбить текст ТЗ на чанки",
                "questions": [],
                "generated_at": generated_at,
            }

        chunk_vectors = await self._embedder.embed(chunks)
        if chunk_vectors is None or len(chunk_vectors) != len(chunks):
            return {
                "tz_found": True,
                "tz_file": tz_file,
                "error": "Не удалось вычислить эмбеддинги чанков ТЗ",
                "questions": [],
                "generated_at": generated_at,
            }

        verdicts: list[dict[str, Any]] = await self._analyze_system(chunks, profile_facts)
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
        }

    # ------------------------------------------------------------------ #
    # Системные обязательные проверки (Stage A: batch LLM; Stage B: matcher)
    # ------------------------------------------------------------------ #
    def _select_relevant_chunks(self, patterns: list[str], chunks: list[str]) -> list[int]:
        """Индексы чанков, задевающих хотя бы один из паттернов проверки."""
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        return [
            idx for idx, chunk in enumerate(chunks) if any(pat.search(chunk) for pat in compiled)
        ]

    async def _analyze_system(
        self,
        chunks: list[str],
        profile_facts: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Системные проверки: лексический отбор секций → 1 LLM-вызов → matcher."""
        selected: set[int] = set()
        for patterns in SYSTEM_RETRIEVAL_PATTERNS.values():
            selected.update(self._select_relevant_chunks(patterns, chunks))
        if not selected:
            # В ТЗ нет ни одной стандартной секции стоп-условий: LLM не зовём.
            return [self._system_skip(qid) for qid in _SYSTEM_TEXT]
        context = "\n\n---\n\n".join(chunks[idx] for idx in sorted(selected))
        system, user = build_batch_system_messages(context)
        data = await self._llm.chat_json(system, user)
        if data is None:
            return [
                self._system_failed(qid, "LLM-извлечение фактов не выполнено (сбой)")
                for qid in _SYSTEM_TEXT
            ]
        extractions: dict[str, Any] = {
            key: (data.get(key) if isinstance(data.get(key), dict) else None) for key in _BATCH_KEYS
        }
        return apply_profile_facts(extractions, profile_facts)

    def _system_skip(self, question_id: str) -> dict[str, Any]:
        return QuestionVerdict(
            question_id=question_id,
            question_text=_SYSTEM_TEXT[question_id],
            verdict=VERDICT_NONE,
            severity=0,
            marker=MARKERS[VERDICT_NONE],
            reasoning="В ТЗ не найдено упоминаний, релевантных проверке",
            source="system",
            question_version=SYSTEM_QUESTIONS_VERSION,
        ).model_dump()

    def _system_failed(self, question_id: str, reason: str) -> dict[str, Any]:
        return QuestionVerdict(
            question_id=question_id,
            question_text=_SYSTEM_TEXT[question_id],
            verdict=VERDICT_NONE,
            severity=0,
            marker=MARKERS[VERDICT_NONE],
            reasoning=reason,
            source="system",
            question_version=SYSTEM_QUESTIONS_VERSION,
        ).model_dump()

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
        """Вердикт по одному вопросу профиля (best-effort: сбой → no_stop_condition)."""
        q_vector = self._question_embedding_cache.get(question_id)
        if q_vector is None:
            q_vector = await self._embedder.embed_one(question_text)
            if q_vector is None:
                return self._profile_verdict(
                    question_id,
                    question_text,
                    VERDICT_NONE,
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
                VERDICT_NONE,
                "LLM-верификация не выполнена (сбой)",
                None,
            )

        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            verdict = VERDICT_NONE
        return self._profile_verdict(
            question_id,
            question_text,
            verdict,
            str(data.get("reasoning") or ""),
            str(data.get("excerpt") or "")[:500] or None,
        )

    def _profile_verdict(
        self,
        question_id: str,
        question_text: str,
        verdict: str,
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
