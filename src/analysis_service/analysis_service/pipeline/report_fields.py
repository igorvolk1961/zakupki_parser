"""Конструктор отчётных полей (FR-12.2): извлечение значений произвольных
пользовательских полей профиля из документов закупки.

Поля — общий, доменно-нейтральный механизм (см. модуль ``rag.py``): имя и
подсказка задаются пользователем на обычном языке, RAG-запрос авто-выводится
из них же, промпт зависит только от ТИПА поля (строка/число/дата/да-нет/
список), не от предметной области. Переиспользует уже посчитанные чанки/
эмбеддинги документов закупки (``RagAnalyzer._collect_document_chunks``) —
эмбеддинги документов не пересчитываются.

Условие поля (``scoring_common.conditions``) проверяет КОД по извлечённому
значению — кроме оператора ``llm`` («соответствует по смыслу»), который
оценивает LLM в том же вызове. Извлечённое значение сохраняется вместе с
``extraction_key``, чтобы API мог пересчитать условие без LLM после правки.

Поле-список: LLM видит только top-k фрагментов и даёт образцы, полноту и
достоверность обеспечивает код (``_complete_list``): значения проверяются
по полному тексту документов, список дополняется повторным LLM-вызовом на
участке текста вокруг найденных значений и поиском по форме кода.

В отличие от ``RagAnalyzer.fill_requirements_data`` (последовательный вызов
LLM на каждый элемент требований), извлечение полей одной закупки идёт
параллельно (``asyncio.gather`` под ограниченным семафором) — иначе для N
пользовательских полей время анализа росло бы линейно.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from analysis_service.llm import LlmClient
from analysis_service.pipeline.prompts import build_field_extract_messages
from analysis_service.settings import Settings
from scoring_common.conditions import (
    apply_conditions,
    canonical_value,
    classify_value,
    extraction_key,
    find_value,
    normalize_text,
    value_shape,
)
from scoring_common.embeddings import Embeddable, cosine_similarity
from scoring_common.sources.matching import (
    DEFAULT_MAX_WINDOW,
    DEFAULT_WINDOW,
    TextWindows,
    source_contexts,
)

logger = logging.getLogger(__name__)

FIELD_TYPES = ("string", "number", "date", "boolean", "list")
# Окна значений в ТЗ хранятся в отчёте: не больше стольких значений и символов.
_MAX_TZ_WINDOWS = 200
_TZ_WINDOW_CHARS = 600


class ReportFieldValue(BaseModel):
    """Значение одного отчётного поля для одной закупки."""

    field_id: str
    field_name: str
    field_type: Literal["string", "number", "date", "boolean", "list"]
    unit: str | None = None
    found: bool
    value: str | float | bool | list[str] | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    excerpt: str | None = Field(default=None, description="цитата фрагмента-источника")
    source_file: str | None = Field(default=None, description="документ, откуда взято значение")
    reasoning: str = Field(default="", description="причина сбоя/пропуска (best-effort)")
    # Отпечаток описания поля на момент извлечения (scoring_common.conditions.
    # extraction_key): совпадает с текущим — условие можно пересчитать без LLM.
    extraction_key: str = ""
    # Условие поля (копия из профиля) и итог его проверки кодом; ``match`` —
    # None, если условие не задано или проверить не удалось (check_status).
    condition: dict[str, Any] | None = None
    match: bool | None = None
    check_status: str = "no_condition"
    mismatched_values: list[str] = Field(default_factory=list)
    # Оценка LLM «соответствует по смыслу» (только условие op=llm) — хранится
    # отдельно от match, чтобы пересчёт не терял её.
    llm_match: bool | None = None
    # Блокирует ли несоответствие (match=False) приемлемость закупки — копия
    # флага из профиля (``report_fields[].blocking``), для удобства фронта/Excel.
    blocking: bool = False
    # Только поле-список: сколько значений дал каждый способ
    # ({"llm", "span", "pattern"}), текстовые значения LLM, не найденные в
    # документах дословно (оставлены), и коды, которых в документах нет
    # (отброшены как выдуманные).
    value_sources: dict[str, int] | None = None
    unconfirmed_values: list[str] = Field(default_factory=list)
    rejected_values: list[str] = Field(default_factory=list)
    # Окно каждого значения в документах ТЗ (до следующего значения той же
    # формы, scoring_common.sources.matching): по нему условие с «рядом»
    # определяет, какие уточнения требуются значению (код A — утилизация),
    # и пересчитывается без повторного чтения документов.
    tz_windows: dict[str, str] = Field(default_factory=dict)
    # Сравнение с сайтом (условие value_kind=url): причины несоответствия
    # значений, требования к каждому значению, найденные словоформы уточнений,
    # состояние текста сайта (собирается / полный / нет).
    mismatch_reasons: dict[str, str] = Field(default_factory=dict)
    requirements: dict[str, list[str]] = Field(default_factory=dict)
    near_matches: dict[str, list[str]] = Field(default_factory=dict)
    # Слово из ТЗ -> метка сайта («утилизации» -> «Утилизация»), None — нет метки.
    near_labels: dict[str, str | None] = Field(default_factory=dict)
    source_status: dict[str, Any] | None = None


@dataclass
class _Source:
    text: str
    norm: str
    # (индекс чанка, начало, конец) в ``text``.
    chunks: list[tuple[int, int, int]] = field(default_factory=list)


class _Corpus:
    """Полный текст документов закупки по источникам (для проверки списков).

    Текст источника — его чанки подряд через пустую строку (чанки — разделы
    документа), позиции чанков запоминаются: так видно, какую часть текста
    LLM уже получала в контексте.
    """

    def __init__(self, chunks: list[str], sources: list[str]) -> None:
        self.sources: dict[str, _Source] = {}
        for idx, (chunk, name) in enumerate(zip(chunks, sources, strict=False)):
            src = self.sources.setdefault(name, _Source("", ""))
            start = len(src.text) + (2 if src.text else 0)
            src.text = f"{src.text}\n\n{chunk}" if src.text else chunk
            src.chunks.append((idx, start, start + len(chunk)))
        for src in self.sources.values():
            src.norm = normalize_text(src.text)
        self._windows: TextWindows | None = None

    def find(self, value: str, mode: str) -> list[tuple[str, int, int]]:
        return [
            (name, s, e)
            for name, src in self.sources.items()
            for s, e in find_value(src.norm, value, mode)
        ]

    def span_around(
        self, positions: list[tuple[str, int, int]], margin: int, max_len: int
    ) -> tuple[str, int, int] | None:
        """Участок вокруг найденных значений: источник с наибольшим их числом,
        от первого до последнего ± ``margin``; длиннее ``max_len`` — окно
        ``max_len`` с наибольшим числом значений."""
        if not positions:
            return None
        counts: dict[str, int] = {}
        for name, _, _ in positions:
            counts[name] = counts.get(name, 0) + 1
        name = max(counts, key=lambda n: counts[n])
        starts = sorted(s for n, s, _ in positions if n == name)
        ends = [e for n, _, e in positions if n == name]
        text_len = len(self.sources[name].text)
        start, end = max(0, starts[0] - margin), min(text_len, max(ends) + margin)
        if end - start > max_len:
            best_i, best_n = 0, 0
            for i, s in enumerate(starts):
                n = sum(1 for t in starts[i:] if t < s + max_len)
                if n > best_n:
                    best_i, best_n = i, n
            start = max(0, starts[best_i] - margin)
            end = min(text_len, start + max_len)
        return name, start, end

    def windows(self) -> TextWindows:
        """Документы ТЗ как «страницы» для окон значений (граница окна — документ)."""
        if self._windows is None:
            self._windows = TextWindows(src.text for src in self.sources.values())
        return self._windows

    def span_seen(self, span: tuple[str, int, int], seen_chunks: set[int]) -> bool:
        name, start, end = span
        overlapping = [i for i, s, e in self.sources[name].chunks if s < end and e > start]
        return all(i in seen_chunks for i in overlapping)


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
        # Поля приходят из профиля уже в каноническом виде (проверяются при
        # записи профиля, scoring_common.conditions.normalize_report_fields).
        active = [
            f
            for f in fields
            if isinstance(f, dict) and f.get("id") and str(f.get("name") or "").strip()
        ]
        if not active:
            return []
        corpus = _Corpus(chunks, chunk_sources)
        sem = asyncio.Semaphore(self._settings.report_field_concurrency)

        async def _bounded(field_def: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await self._extract_one(
                    field_def, chunks, chunk_vectors, chunk_sources, corpus
                )

        results = await asyncio.gather(*(_bounded(f) for f in active))
        values = [self._with_tz_windows(v, f, corpus) for v, f in zip(results, active, strict=True)]
        # Условия проверяются по отчёту целиком: уточнения «рядом» — значения
        # соседнего поля; текст сайтов из условий — из хранилища (S3).
        sources = await asyncio.to_thread(source_contexts, active)
        checked = apply_conditions(values, active, sources)
        for value, field_def in zip(checked, active, strict=True):
            if value.get("reasoning") and field_def.get("condition"):
                # Сбой извлечения — условие не проверено, а не «не найдено в ТЗ».
                value["check_status"] = "llm_failed"
        return checked

    @staticmethod
    def _with_tz_windows(
        value: dict[str, Any], field_def: dict[str, Any], corpus: _Corpus
    ) -> dict[str, Any]:
        """Окна значений строки/списка в документах ТЗ (для уточнений «рядом»)."""
        if not value.get("found") or value.get("field_type") not in ("string", "list"):
            return value
        raw = value.get("value")
        items = [str(v) for v in raw] if isinstance(raw, list) else [str(raw)]
        mode = str(field_def.get("value_mode") or "auto")
        near = (field_def.get("condition") or {}).get("near") or {}
        windows = corpus.windows()
        found: dict[str, str] = {}
        for item in items[:_MAX_TZ_WINDOWS]:
            places = windows.occurrences(item, mode)
            if places:
                page, start, end = places[0]
                found[item] = windows.window(
                    page,
                    start,
                    end,
                    item,
                    mode,
                    window=int(near.get("window", DEFAULT_WINDOW)),
                    max_window=int(near.get("max_window", DEFAULT_MAX_WINDOW)),
                )[:_TZ_WINDOW_CHARS]
        return {**value, "tz_windows": found}

    async def _extract_one(
        self,
        field_def: dict[str, Any],
        chunks: list[str],
        chunk_vectors: list[list[float]],
        chunk_sources: list[str],
        corpus: _Corpus,
    ) -> dict[str, Any]:
        field_id = str(field_def.get("id") or "")
        field_name = str(field_def.get("name") or "").strip()
        raw_type = field_def.get("type")
        field_type = raw_type if raw_type in FIELD_TYPES else "string"
        unit = str(field_def["unit"]) if field_def.get("unit") else None
        base: dict[str, Any] = {
            "field_id": field_id,
            "field_name": field_name,
            "field_type": field_type,
            "unit": unit,
            "found": False,
            "extraction_key": extraction_key(field_def),
        }

        query = f"{field_name}. {str(field_def.get('hint') or '').strip()}".strip()
        f_vector = self._field_embedding_cache.get(field_id)
        if f_vector is None:
            f_vector = await self._embedder.embed_one(query)
            if f_vector is None:
                return self._finish(
                    base,
                    field_def,
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

        system, user = build_field_extract_messages(field_def, context)
        data = await self._llm.chat_json(system, user)
        if data is None:
            return self._finish(base, field_def, reasoning="LLM-извлечение не выполнено (сбой)")

        found = bool(data.get("found"))
        raw_confidence = data.get("confidence")
        confidence: Literal["high", "medium", "low"] = (
            raw_confidence if raw_confidence in ("high", "medium", "low") else "low"
        )
        raw_match = data.get("match")
        base.update(
            confidence=confidence,
            excerpt=(str(data.get("excerpt") or "")[:500] or None) if found else None,
            source_file=chunk_sources[top_idx[0]] if found and top_idx else None,
            llm_match=(
                raw_match
                if found
                and isinstance(raw_match, bool)
                and (field_def.get("condition") or {}).get("op") == "llm"
                else None
            ),
        )
        if field_type == "list":
            values = _coerce_list(data.get("value")) if found else []
            base.update(await self._complete_list(field_def, values, set(top_idx), corpus))
            base["found"] = bool(base["value"])
            return self._finish(base, field_def)
        value = self._coerce_value(data.get("value") if found else None, field_type)
        base.update(found=found and value is not None, value=value)
        return self._finish(base, field_def)

    async def _complete_list(
        self,
        field_def: dict[str, Any],
        llm_values: list[str],
        seen_chunks: set[int],
        corpus: _Corpus,
    ) -> dict[str, Any]:
        """Проверка и дополнение списка по полному тексту документов закупки."""
        mode = str(field_def.get("value_mode") or "auto")
        result: list[str] = []
        keys: set[str] = set()
        unconfirmed: list[str] = []
        rejected: list[str] = []
        positions: list[tuple[str, int, int]] = []
        sources = {"llm": 0, "span": 0, "pattern": 0}

        def add(value: str, origin: str, spans: list[tuple[str, int, int]] | None = None) -> None:
            """Проверенное значение в итоговый список (без повторов)."""
            key = canonical_value(value, mode)
            if not key or key in keys:
                return
            if spans is None:
                spans = corpus.find(value, mode)
            if not spans and classify_value(value, mode) == "code":
                rejected.append(value)
                return
            keys.add(key)
            result.append(value)
            sources[origin] += 1
            if spans:
                positions.extend(spans)
            else:
                unconfirmed.append(value)

        for value in llm_values:
            add(value, "llm")

        if field_def.get("extend_list", True) and positions:
            span = corpus.span_around(
                positions, self._settings.list_span_margin, self._settings.list_span_max
            )
            if span is not None and not corpus.span_seen(span, seen_chunks):
                name, start, end = span
                context = f"[Источник: {name}]\n{corpus.sources[name].text[start:end]}"
                system, user = build_field_extract_messages(field_def, context)
                extra = await self._llm.chat_json(system, user)
                if isinstance(extra, dict) and extra.get("found"):
                    for value in _coerce_list(extra.get("value")):
                        add(value, "span")
            shapes = {
                shape.pattern: shape
                for value in result
                if value not in unconfirmed
                and classify_value(value, mode) == "code"
                and (shape := value_shape(value)) is not None
            }
            for shape in shapes.values():
                for name, src in corpus.sources.items():
                    for m in shape.finditer(src.norm):
                        add(src.text[m.start() : m.end()], "pattern", [(name, *m.span())])

        return {
            "value": result,
            "value_sources": sources,
            "unconfirmed_values": unconfirmed[:50],
            "rejected_values": rejected[:50],
        }

    @staticmethod
    def _finish(
        base: dict[str, Any], field_def: dict[str, Any], *, reasoning: str = ""
    ) -> dict[str, Any]:
        """Извлечённое значение поля (условие проверяется в ``extract`` по отчёту)."""
        payload = dict(base)
        if reasoning:
            payload["reasoning"] = reasoning
            payload["found"] = False
        if not payload.get("found"):
            payload["value"] = None
        return ReportFieldValue(**payload).model_dump()

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


def _coerce_list(value: Any) -> list[str]:
    """Список значений из ответа LLM (массив или строка через перевод строки/«;»)."""
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).replace(";", "\n").split("\n")
    return [s for s in (str(i).strip() for i in items if i is not None) if s]
