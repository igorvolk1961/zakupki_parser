"""Unit-тесты конструктора отчётных полей (analysis_service.pipeline.report_fields)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from analysis_service.pipeline.prompts import (
    FIELD_EXTRACT_SYSTEM_BY_TYPE,
    build_field_extract_messages,
)
from analysis_service.pipeline.report_fields import ReportFieldExtractor
from analysis_service.settings import Settings

# --- Промпты ---------------------------------------------------------------


def test_build_field_extract_messages_substitutes_name_and_hint() -> None:
    field = {"id": "f1", "name": "код ФККО", "hint": "код отхода по ФККО", "type": "string"}
    system, user = build_field_extract_messages(field, "Чанк 1")
    assert "код ФККО" in user
    assert "код отхода по ФККО" in user
    assert "Чанк 1" in user
    assert "{field_name}" not in user and "{field_hint}" not in user and "{context}" not in user


def test_build_field_extract_messages_type_selects_prompt() -> None:
    system_number, _ = build_field_extract_messages({"type": "number"}, "")
    system_date, _ = build_field_extract_messages({"type": "date"}, "")
    system_boolean, _ = build_field_extract_messages({"type": "boolean"}, "")
    system_unknown, _ = build_field_extract_messages({"type": "enum"}, "")
    assert system_number == FIELD_EXTRACT_SYSTEM_BY_TYPE["number"]
    assert system_date == FIELD_EXTRACT_SYSTEM_BY_TYPE["date"]
    assert system_boolean == FIELD_EXTRACT_SYSTEM_BY_TYPE["boolean"]
    # Неизвестный/отсутствующий тип — фолбэк на самый общий (string).
    assert system_unknown == FIELD_EXTRACT_SYSTEM_BY_TYPE["string"]


def test_field_extract_prompts_are_domain_generic() -> None:
    """Промпты зависят только от типа поля — никаких доменных слов не зашито."""
    domain_words = ("фкко", "отход", "лицензи", "утилизац", "транспорт")
    for system_prompt in FIELD_EXTRACT_SYSTEM_BY_TYPE.values():
        lowered = system_prompt.lower()
        for word in domain_words:
            assert word not in lowered


# --- Фейки -------------------------------------------------------------


class _RecordingEmbedder:
    """Детерминированный эмбеддер, запоминающий поисковые запросы полей."""

    def __init__(self) -> None:
        self.embed_one_calls: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(i)] for i, _ in enumerate(texts)]

    async def embed_one(self, text: str) -> list[float] | None:
        self.embed_one_calls.append(text)
        return [1.0, 0.0]


class _RecordingLlm:
    """Возвращает ответы по очереди на каждый вызов; запоминает user-промпты."""

    def __init__(self, responses: dict[str, dict[str, Any] | None]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def chat_json(self, system: str, user: str) -> dict[str, Any] | None:
        self.calls.append(user)
        for field_name, response in self._responses.items():
            if field_name in user:
                return response
        return None


class _ConcurrencyTrackingLlm:
    """Отслеживает пиковое число одновременно выполняющихся вызовов."""

    def __init__(self, delay: float = 0.05) -> None:
        self._delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def chat_json(self, system: str, user: str) -> dict[str, Any] | None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(self._delay)
        self.in_flight -= 1
        return {"found": True, "value": "x", "confidence": "high", "excerpt": "e"}


def _settings(*, top_k: int = 2, concurrency: int = 4) -> Settings:
    settings = Settings()
    settings.report_field_top_k = top_k
    settings.report_field_concurrency = concurrency
    return settings


def _chunks(n: int) -> tuple[list[str], list[list[float]], list[str]]:
    chunks = [f"чанк {i}" for i in range(n)]
    vectors = [[1.0, float(i)] for i in range(n)]
    sources = [f"doc{i}.docx" for i in range(n)]
    return chunks, vectors, sources


# --- Поведение extract() ----------------------------------------------------


def test_extract_query_is_name_and_hint() -> None:
    embedder = _RecordingEmbedder()
    llm = _RecordingLlm({"код ФККО": {"found": True, "value": "123", "confidence": "high"}})
    extractor = ReportFieldExtractor(_settings(), embedder, llm)  # type: ignore[arg-type]
    field = {"id": "f1", "name": "код ФККО", "hint": "код отхода", "type": "string"}
    chunks, vectors, sources = _chunks(3)

    asyncio.run(extractor.extract([field], chunks, vectors, sources))

    assert embedder.embed_one_calls == ["код ФККО. код отхода"]


def test_extract_uses_report_field_top_k() -> None:
    embedder = _RecordingEmbedder()
    llm = _RecordingLlm({"объём": {"found": True, "value": 4000, "confidence": "high"}})
    extractor = ReportFieldExtractor(_settings(top_k=2), embedder, llm)  # type: ignore[arg-type]
    field = {"id": "f1", "name": "объём", "hint": "", "type": "number"}
    chunks, vectors, sources = _chunks(10)

    asyncio.run(extractor.extract([field], chunks, vectors, sources))

    assert llm.calls[0].count("[Источник:") == 2


def test_extract_found_value_and_source() -> None:
    embedder = _RecordingEmbedder()
    llm = _RecordingLlm(
        {"объём": {"found": True, "value": 4000.5, "confidence": "high", "excerpt": "4000.5 м3"}}
    )
    extractor = ReportFieldExtractor(_settings(), embedder, llm)  # type: ignore[arg-type]
    field = {"id": "f1", "name": "объём", "hint": "", "type": "number", "unit": "м3"}
    chunks, vectors, sources = _chunks(3)

    results = asyncio.run(extractor.extract([field], chunks, vectors, sources))

    assert results == [
        {
            "field_id": "f1",
            "field_name": "объём",
            "field_type": "number",
            "unit": "м3",
            "found": True,
            "value": 4000.5,
            "confidence": "high",
            "excerpt": "4000.5 м3",
            "source_file": "doc0.docx",
            "reasoning": "",
        }
    ]


def test_extract_malformed_response_does_not_raise_and_others_proceed() -> None:
    embedder = _RecordingEmbedder()
    llm = _RecordingLlm({"поле B": {"found": True, "value": "нашли", "confidence": "medium"}})
    extractor = ReportFieldExtractor(_settings(), embedder, llm)  # type: ignore[arg-type]
    fields = [
        {"id": "a", "name": "поле A", "type": "string"},  # нет в responses -> chat_json вернёт None
        {"id": "b", "name": "поле B", "type": "string"},
    ]
    chunks, vectors, sources = _chunks(3)

    results = asyncio.run(extractor.extract(fields, chunks, vectors, sources))

    by_id = {r["field_id"]: r for r in results}
    assert by_id["a"]["found"] is False
    assert by_id["a"]["reasoning"]
    assert by_id["b"]["found"] is True
    assert by_id["b"]["value"] == "нашли"


def test_extract_number_field_uncoercible_value_becomes_not_found_value() -> None:
    """LLM вернул found=true, но нечисловое value для числового поля — value=None."""
    embedder = _RecordingEmbedder()
    llm = _RecordingLlm({"цена": {"found": True, "value": "не указано", "confidence": "low"}})
    extractor = ReportFieldExtractor(_settings(), embedder, llm)  # type: ignore[arg-type]
    field = {"id": "f1", "name": "цена", "type": "number"}
    chunks, vectors, sources = _chunks(3)

    results = asyncio.run(extractor.extract([field], chunks, vectors, sources))

    assert results[0]["found"] is True
    assert results[0]["value"] is None


def test_extract_empty_fields_returns_empty_without_calls() -> None:
    embedder = _RecordingEmbedder()
    llm = _RecordingLlm({})
    extractor = ReportFieldExtractor(_settings(), embedder, llm)  # type: ignore[arg-type]
    chunks, vectors, sources = _chunks(3)

    results = asyncio.run(extractor.extract([], chunks, vectors, sources))

    assert results == []
    assert embedder.embed_one_calls == []
    assert llm.calls == []


# --- Параллелизм: явно НЕ повторяет антипаттерн fill_requirements_data ----


def test_extract_runs_concurrently_not_sequentially() -> None:
    embedder = _RecordingEmbedder()
    llm = _ConcurrencyTrackingLlm(delay=0.05)
    extractor = ReportFieldExtractor(_settings(concurrency=4), embedder, llm)  # type: ignore[arg-type]
    fields = [{"id": str(i), "name": f"поле {i}", "type": "string"} for i in range(4)]
    chunks, vectors, sources = _chunks(3)

    start = time.perf_counter()
    asyncio.run(extractor.extract(fields, chunks, vectors, sources))
    duration = time.perf_counter() - start

    # Последовательно (fill_requirements_data-антипаттерн) заняло бы ~0.2с;
    # параллельно под семафором(4) — ~0.05с. Порог с запасом от дрожания CI.
    assert duration < 0.15
    assert llm.max_in_flight > 1


def test_extract_concurrency_capped_by_semaphore() -> None:
    embedder = _RecordingEmbedder()
    llm = _ConcurrencyTrackingLlm(delay=0.03)
    extractor = ReportFieldExtractor(_settings(concurrency=2), embedder, llm)  # type: ignore[arg-type]
    fields = [{"id": str(i), "name": f"поле {i}", "type": "string"} for i in range(6)]
    chunks, vectors, sources = _chunks(3)

    asyncio.run(extractor.extract(fields, chunks, vectors, sources))

    assert llm.max_in_flight <= 2
