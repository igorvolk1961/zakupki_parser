"""RAG-пайплайн анализа стоп-условий по вопросам клиента.

Для каждого вопроса профиля: эмбеддинг вопроса (кэшируется) → косинусная близость
ко всем чанкам ТЗ → top-k чанков → лёгкая LLM-верификация: содержит ли чанк
стоп-условие и какой степени запрет (absolute/soft/none). Результат — ``rag_report``
для таблицы тендеролога.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from analysis_service.llm import LlmClient
from analysis_service.pipeline.chunker import split_tz_sections
from analysis_service.pipeline.prompts import build_verdict_messages
from analysis_service.settings import Settings
from scoring_common.embeddings import EmbeddingClient, cosine_similarity
from scoring_common.tz import clean_text, extract_text, find_tz_reference

logger = logging.getLogger(__name__)

VERDICT_NONE: Literal["no_stop_condition"] = "no_stop_condition"
VERDICT_SOFT: Literal["soft"] = "soft"
VERDICT_ABSOLUTE: Literal["absolute"] = "absolute"
VERDICTS = (VERDICT_NONE, VERDICT_SOFT, VERDICT_ABSOLUTE)

SEVERITY: dict[str, int] = {VERDICT_NONE: 0, VERDICT_SOFT: 1, VERDICT_ABSOLUTE: 2}


class QuestionVerdict(BaseModel):
    """Вердикт по одному вопросу клиента."""

    question_id: str
    question_text: str
    verdict: Literal["no_stop_condition", "absolute", "soft"]
    severity: int = Field(ge=0, le=2)
    excerpt: str | None = Field(default=None, description="цитата фрагмента ТЗ")
    reasoning: str = Field(default="", description="краткое обоснование")


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
        # Кэш эмбеддингов вопросов (вопросы профиля одинаковы для всех закупок).
        self._question_embedding_cache: dict[str, list[float]] = {}

    async def analyze(
        self, record: dict[str, Any], questions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """RAG-отчёт по вопросам клиента. best-effort: сбой не роняет задание."""
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

        verdicts: list[dict[str, Any]] = []
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

    async def _verdict_for_question(
        self,
        question_id: str,
        question_text: str,
        chunks: list[str],
        chunk_vectors: list[list[float]],
    ) -> dict[str, Any]:
        """Вердикт по одному вопросу (best-effort: сбой → no_stop_condition с пометкой)."""
        q_vector = self._question_embedding_cache.get(question_id)
        if q_vector is None:
            q_vector = await self._embedder.embed_one(question_text)
            if q_vector is None:
                return QuestionVerdict(
                    question_id=question_id,
                    question_text=question_text,
                    verdict=VERDICT_NONE,
                    severity=0,
                    reasoning="Не удалось вычислить эмбеддинг вопроса (анализ пропущен)",
                ).model_dump()
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
            return QuestionVerdict(
                question_id=question_id,
                question_text=question_text,
                verdict=VERDICT_NONE,
                severity=0,
                reasoning="LLM-верификация не выполнена (сбой)",
            ).model_dump()

        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            verdict = VERDICT_NONE
        return QuestionVerdict(
            question_id=question_id,
            question_text=question_text,
            verdict=verdict,
            severity=SEVERITY[verdict],
            excerpt=str(data.get("excerpt") or "")[:500] or None,
            reasoning=str(data.get("reasoning") or ""),
        ).model_dump()
