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

from scoring_common.conditions import extraction_key, normalize_report_fields

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
    # Поля приходят из профиля в каноническом виде (как их отдаёт API).
    [field] = normalize_report_fields(
        [{"id": "f1", "name": "объём", "hint": "", "type": "number", "unit": "м3"}]
    )
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
            "extraction_key": extraction_key(field),
            "condition": None,
            "match": None,
            "check_status": "no_condition",
            "mismatched_values": [],
            "llm_match": None,
            "blocking": False,
            "value_sources": None,
            "unconfirmed_values": [],
            "rejected_values": [],
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
    """LLM вернул found=true, но нечисловое value для числового поля — не найдено."""
    embedder = _RecordingEmbedder()
    llm = _RecordingLlm({"цена": {"found": True, "value": "не указано", "confidence": "low"}})
    extractor = ReportFieldExtractor(_settings(), embedder, llm)  # type: ignore[arg-type]
    field = {"id": "f1", "name": "цена", "type": "number"}
    chunks, vectors, sources = _chunks(3)

    results = asyncio.run(extractor.extract([field], chunks, vectors, sources))

    assert results[0]["found"] is False
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


# --- Условия полей (оператор llm — оценка LLM, остальные — код) -------------


def _llm_condition(value: str) -> dict[str, Any]:
    return {"op": "llm", "value_kind": "scalar", "value": value}


def test_build_field_extract_messages_passes_llm_condition_value() -> None:
    field = {"id": "f1", "name": "объём", "type": "number", "condition": _llm_condition("≥ 500")}
    _, user = build_field_extract_messages(field, "Чанк 1")
    assert "≥ 500" in user
    assert "{expected_value}" not in user


def test_build_field_extract_messages_code_condition_not_sent_to_llm() -> None:
    """Условие, которое проверяет код, в промпт не попадает."""
    field = {
        "id": "f1",
        "name": "объём",
        "type": "number",
        "condition": {"op": "gte", "value_kind": "scalar", "value": "500"},
    }
    _, user = build_field_extract_messages(field, "")
    assert "не задано" in user
    assert "500" not in user


def test_build_field_extract_messages_condition_defaults_to_not_set() -> None:
    _, user = build_field_extract_messages({"id": "f1", "name": "объём", "type": "number"}, "")
    assert "не задано" in user


def _run_one(field: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    llm = _RecordingLlm({field["name"]: response})
    extractor = ReportFieldExtractor(_settings(), _RecordingEmbedder(), llm)  # type: ignore[arg-type]
    chunks, vectors, sources = _chunks(3)
    return asyncio.run(extractor.extract([field], chunks, vectors, sources))[0]


def test_extract_llm_condition_uses_llm_judgement() -> None:
    field = {
        "id": "f1",
        "name": "объём",
        "type": "number",
        "condition": _llm_condition("не менее 500"),
        "blocking": True,
    }
    result = _run_one(field, {"found": True, "value": 600, "confidence": "high", "match": True})
    assert result["condition"] == _llm_condition("не менее 500")
    assert result["match"] is True
    assert result["llm_match"] is True
    assert result["check_status"] == "ok"
    assert result["blocking"] is True


def test_extract_llm_match_ignored_without_condition() -> None:
    """LLM может вернуть match, но без условия он не учитывается."""
    field = {"id": "f1", "name": "объём", "type": "number"}
    result = _run_one(field, {"found": True, "value": 600, "confidence": "high", "match": True})
    assert result["condition"] is None
    assert result["match"] is None
    assert result["blocking"] is False


def test_extract_match_none_when_not_found() -> None:
    field = {"id": "f1", "name": "объём", "type": "number", "condition": _llm_condition("≥ 500")}
    result = _run_one(field, {"found": False, "confidence": "low"})
    assert result["found"] is False
    assert result["match"] is None
    assert result["check_status"] == "not_found_in_tz"


def test_extract_llm_condition_mismatch() -> None:
    field = {
        "id": "f1",
        "name": "объём",
        "type": "number",
        "condition": _llm_condition("не менее 500"),
        "blocking": True,
    }
    result = _run_one(field, {"found": True, "value": 100, "confidence": "high", "match": False})
    assert result["match"] is False
    assert result["blocking"] is True


def test_extract_code_condition_checked_by_code_not_llm() -> None:
    """Оператор сравнения проверяется кодом: ответ LLM match не влияет."""
    field = {
        "id": "f1",
        "name": "объём",
        "type": "number",
        "condition": {"op": "gte", "value_kind": "scalar", "value": "500"},
        "blocking": True,
    }
    result = _run_one(field, {"found": True, "value": 100, "confidence": "high", "match": True})
    assert result["match"] is False
    assert result["check_status"] == "ok"
    assert result["llm_match"] is None


def test_extract_llm_failure_marks_condition_unchecked() -> None:
    field = {"id": "f1", "name": "объём", "type": "number", "condition": _llm_condition("≥ 5")}
    llm = _RecordingLlm({})  # chat_json -> None
    extractor = ReportFieldExtractor(_settings(), _RecordingEmbedder(), llm)  # type: ignore[arg-type]
    chunks, vectors, sources = _chunks(3)
    result = asyncio.run(extractor.extract([field], chunks, vectors, sources))[0]
    assert result["match"] is None
    assert result["check_status"] == "llm_failed"


# --- Поле-список: проверка по тексту и дополнение ---------------------------

_TABLE = (
    "Таблица отходов\n"
    "| 1 | 1 11 010 21 49 2 | семена протравленные | 2 |\n"
    "| 2 | 4 71 101 01 52 1 | лампы ртутные | 1 |\n"
    "| 3 | 7 33 100 01 72 4 | мусор от офисных помещений | 4 |\n"
    "Контактный телефон 8 912 345 67 89"
)


def _list_field(**extra: Any) -> dict[str, Any]:
    return {"id": "f1", "name": "коды ФККО", "type": "list", **extra}


class _QueueLlm:
    """Отдаёт ответы по очереди; запоминает user-промпты."""

    def __init__(self, responses: list[dict[str, Any] | None]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def chat_json(self, system: str, user: str) -> dict[str, Any] | None:
        self.calls.append(user)
        return self._responses.pop(0) if self._responses else None


def _run_list(
    field: dict[str, Any],
    responses: list[dict[str, Any] | None],
    chunks: list[str],
    *,
    top_k: int = 5,
) -> tuple[dict[str, Any], _QueueLlm]:
    llm = _QueueLlm(responses)
    extractor = ReportFieldExtractor(_settings(top_k=top_k), _RecordingEmbedder(), llm)  # type: ignore[arg-type]
    vectors = [[1.0, float(i)] for i in range(len(chunks))]
    sources = ["ТЗ.docx"] * len(chunks)
    result = asyncio.run(extractor.extract([field], chunks, vectors, sources))[0]
    return result, llm


def test_list_code_absent_from_documents_is_rejected() -> None:
    result, _ = _run_list(
        _list_field(extend_list=False),
        [{"found": True, "value": ["1 11 010 21 49 2", "9 99 999 99 99 9"], "confidence": "high"}],
        [_TABLE],
    )
    assert result["value"] == ["1 11 010 21 49 2"]
    assert result["rejected_values"] == ["9 99 999 99 99 9"]


def test_list_text_not_found_verbatim_is_kept_unconfirmed() -> None:
    result, _ = _run_list(
        _list_field(extend_list=False),
        [{"found": True, "value": ["Лампы ртутные", "бумага"], "confidence": "high"}],
        [_TABLE],
    )
    assert result["value"] == ["Лампы ртутные", "бумага"]
    assert result["unconfirmed_values"] == ["бумага"]
    assert result["rejected_values"] == []


def test_list_extended_by_shape_without_phone_numbers() -> None:
    """LLM вернула один код — остальные коды той же формы добираются кодом."""
    result, llm = _run_list(
        _list_field(),
        [{"found": True, "value": ["1 11 010 21 49 2"], "confidence": "high"}],
        [_TABLE],
    )
    assert result["value"] == ["1 11 010 21 49 2", "4 71 101 01 52 1", "7 33 100 01 72 4"]
    assert result["value_sources"] == {"llm": 1, "span": 0, "pattern": 2}
    # Весь текст LLM уже видела — повторного вызова на участке нет.
    assert len(llm.calls) == 1


def test_list_codes_without_separators_do_not_generalize() -> None:
    chunks = ["коды: 11101021492, 47110101521; тел. 89123456789"]
    result, _ = _run_list(
        _list_field(),
        [{"found": True, "value": ["11101021492"], "confidence": "high"}],
        chunks,
    )
    assert result["value"] == ["11101021492"]


def test_list_span_reextraction_when_llm_did_not_see_the_table() -> None:
    """Таблица разбита на чанки, LLM видела только первый — участок отдаётся повторно."""
    rows = [f"| {i} | наименование отхода номер {i} |" for i in range(40)]
    chunks = ["\n".join(rows[:20]), "\n".join(rows[20:])]
    result, llm = _run_list(
        _list_field(value_mode="text"),
        [
            {"found": True, "value": ["наименование отхода номер 1"], "confidence": "high"},
            {
                "found": True,
                "value": ["наименование отхода номер 1", "наименование отхода номер 35"],
                "confidence": "high",
            },
        ],
        chunks,
        top_k=1,
    )
    assert len(llm.calls) == 2
    assert "номер 35" in llm.calls[1]
    assert result["value"] == ["наименование отхода номер 1", "наименование отхода номер 35"]
    assert result["value_sources"]["span"] == 1


def test_list_extension_can_be_disabled() -> None:
    result, llm = _run_list(
        _list_field(extend_list=False),
        [{"found": True, "value": ["1 11 010 21 49 2"], "confidence": "high"}],
        [_TABLE],
    )
    assert result["value"] == ["1 11 010 21 49 2"]
    assert len(llm.calls) == 1


def test_list_duplicates_in_other_notation_are_merged() -> None:
    result, _ = _run_list(
        _list_field(extend_list=False),
        [{"found": True, "value": ["1 11 010 21 49 2", "11101021492"], "confidence": "high"}],
        [_TABLE],
    )
    assert result["value"] == ["1 11 010 21 49 2"]


def test_list_all_in_condition_reports_missing_values() -> None:
    field = _list_field(
        condition={"op": "all_in", "value": ["1 11 010 21 49 2", "7 33 100 01 72 4"]},
        blocking=True,
    )
    result, _ = _run_list(
        field,
        [{"found": True, "value": ["1 11 010 21 49 2"], "confidence": "high"}],
        [_TABLE],
    )
    assert result["match"] is False
    assert result["mismatched_values"] == ["4 71 101 01 52 1"]
    assert result["blocking"] is True


def test_list_nothing_found_is_not_found() -> None:
    result, _ = _run_list(
        _list_field(condition={"op": "all_in", "value": ["1 11 010 21 49 2"]}),
        [{"found": False, "value": None, "confidence": "low"}],
        [_TABLE],
    )
    assert result["found"] is False
    assert result["value"] is None
    assert result["check_status"] == "not_found_in_tz"
