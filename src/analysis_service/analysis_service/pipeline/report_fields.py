"""Конструктор отчётных полей (FR-12.2): извлечение значений произвольных
пользовательских полей профиля из документов закупки.

Поля — общий, доменно-нейтральный механизм (см. модуль ``rag.py``): имя и
подсказка задаются пользователем на обычном языке, RAG-запрос авто-выводится
из них же, промпт зависит только от ТИПА поля (строка/число/дата/да-нет), не
от предметной области. Переиспользует уже посчитанные чанки/эмбеддинги
документов закупки (``RagAnalyzer._collect_document_chunks``) — эмбеддинги
документов не пересчитываются.

В отличие от ``RagAnalyzer.fill_requirements_data`` (последовательный вызов
LLM на каждый элемент требований), извлечение полей одной закупки идёт
параллельно (``asyncio.gather`` под ограниченным семафором) — иначе для N
пользовательских полей время анализа росло бы линейно.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from analysis_service.llm import LlmClient
from analysis_service.pipeline.prompts import build_field_extract_messages
from analysis_service.settings import Settings
from scoring_common.embeddings import Embeddable, cosine_similarity

logger = logging.getLogger(__name__)

FIELD_TYPES = ("string", "number", "date", "boolean")


class ReportFieldValue(BaseModel):
    """Значение одного отчётного поля для одной закупки."""

    field_id: str
    field_name: str
    field_type: Literal["string", "number", "date", "boolean"]
    unit: str | None = None
    found: bool
    value: str | float | bool | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    excerpt: str | None = Field(default=None, description="цитата фрагмента-источника")
    source_file: str | None = Field(default=None, description="документ, откуда взято значение")
    reasoning: str = Field(default="", description="причина сбоя/пропуска (best-effort)")
    # Ожидаемое значение (профиль, FR-13.5) — задаётся свободным текстом
    # (может быть условием, напр. «не менее 500000», не только точным
    # значением); ``match`` — оценка LLM «найденное соответствует ожидаемому»,
    # None — ожидаемое значение не задано ИЛИ поле не найдено.
    expected_value: str | None = None
    match: bool | None = None
    # Блокирует ли несоответствие (match=False) приемлемость закупки — копия
    # флага из профиля (``report_fields[].blocking``), для удобства фронта/Excel.
    blocking: bool = False


class ReportFieldExtractor:
    """Извлекает значения отчётных полей профиля для одной закупки."""

    def __init__(self, settings: Settings, embedder: Embeddable, llm: LlmClient) -> None:
        self._settings = settings
        self._embedder = embedder
        self._llm = llm
        # Эмбеддинги запросов полей кэшируются по field_id (поля профиля одинаковы
        # для всех закупок, как и вопросы — см. RagAnalyzer._question_embedding_cache).
        self._field_embedding_cache: dict[str, list[float]] = {}

    async def extract(
        self,
        fields: list[dict[str, Any]],
        chunks: list[str],
        chunk_vectors: list[list[float]],
        chunk_sources: list[str],
    ) -> list[dict[str, Any]]:
        """Значения всех активных полей — параллельно, под ограниченным семафором."""
        active = [f for f in fields if str(f.get("id") or "") and str(f.get("name") or "").strip()]
        if not active:
            return []
        sem = asyncio.Semaphore(self._settings.report_field_concurrency)

        async def _bounded(field: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await self._extract_one(field, chunks, chunk_vectors, chunk_sources)

        results = await asyncio.gather(*(_bounded(f) for f in active))
        return list(results)

    async def _extract_one(
        self,
        field: dict[str, Any],
        chunks: list[str],
        chunk_vectors: list[list[float]],
        chunk_sources: list[str],
    ) -> dict[str, Any]:
        field_id = str(field.get("id") or "")
        field_name = str(field.get("name") or "").strip()
        raw_type = field.get("type")
        field_type = raw_type if raw_type in FIELD_TYPES else "string"
        unit = str(field["unit"]) if field.get("unit") else None
        expected_value = str(field.get("expected_value") or "").strip() or None
        blocking = bool(field.get("blocking"))

        query = f"{field_name}. {str(field.get('hint') or '').strip()}".strip()
        f_vector = self._field_embedding_cache.get(field_id)
        if f_vector is None:
            f_vector = await self._embedder.embed_one(query)
            if f_vector is None:
                return self._value(
                    field_id,
                    field_name,
                    field_type,
                    unit,
                    expected_value=expected_value,
                    blocking=blocking,
                    reasoning="Не удалось вычислить эмбеддинг запроса поля (анализ пропущен)",
                )
            self._field_embedding_cache[field_id] = f_vector

        scored = sorted(
            ((cosine_similarity(f_vector, cv), idx) for idx, cv in enumerate(chunk_vectors)),
            reverse=True,
        )
        top_idx = [idx for _, idx in scored[: self._settings.report_field_top_k]]
        context = "\n\n---\n\n".join(
            f"[Источник: {chunk_sources[idx]}]\n{chunks[idx]}" for idx in top_idx
        )

        system, user = build_field_extract_messages(field, context)
        data = await self._llm.chat_json(system, user)
        if data is None:
            return self._value(
                field_id,
                field_name,
                field_type,
                unit,
                expected_value=expected_value,
                blocking=blocking,
                reasoning="LLM-извлечение не выполнено (сбой)",
            )

        found = bool(data.get("found"))
        value = data.get("value") if found else None
        raw_confidence = data.get("confidence")
        confidence: Literal["high", "medium", "low"] = (
            raw_confidence if raw_confidence in ("high", "medium", "low") else "low"
        )
        source_file = chunk_sources[top_idx[0]] if found and top_idx else None
        raw_match = data.get("match")
        match = raw_match if found and expected_value and isinstance(raw_match, bool) else None
        return self._value(
            field_id,
            field_name,
            field_type,
            unit,
            found=found,
            value=self._coerce_value(value, field_type),
            confidence=confidence,
            excerpt=(str(data.get("excerpt") or "")[:500] or None) if found else None,
            source_file=source_file,
            expected_value=expected_value,
            match=match,
            blocking=blocking,
        )

    @staticmethod
    def _coerce_value(value: Any, field_type: str) -> str | float | bool | None:
        """Значение LLM приводится к типу поля; несовместимое значение — не найдено."""
        if value is None:
            return None
        if field_type == "number":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        if field_type == "boolean":
            return value if isinstance(value, bool) else None
        return str(value)

    @staticmethod
    def _value(
        field_id: str,
        field_name: str,
        field_type: str,
        unit: str | None,
        *,
        found: bool = False,
        value: str | float | bool | None = None,
        confidence: Literal["high", "medium", "low"] = "low",
        excerpt: str | None = None,
        source_file: str | None = None,
        reasoning: str = "",
        expected_value: str | None = None,
        match: bool | None = None,
        blocking: bool = False,
    ) -> dict[str, Any]:
        return ReportFieldValue(
            field_id=field_id,
            field_name=field_name,
            field_type=field_type,  # type: ignore[arg-type]
            unit=unit,
            found=found,
            value=value if found else None,
            confidence=confidence,
            excerpt=excerpt,
            source_file=source_file,
            reasoning=reasoning,
            expected_value=expected_value,
            match=match,
            blocking=blocking,
        ).model_dump()
