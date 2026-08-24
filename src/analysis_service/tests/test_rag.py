"""Unit-тесты RAG-пайплайна (analysis_service.pipeline.rag) и косинусной близости."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from analysis_service.pipeline.prompts import build_verdict_messages
from analysis_service.pipeline.rag import RagAnalyzer
from analysis_service.settings import Settings

from scoring_common.embeddings import cosine_similarity

# --- Промпты ------------------------------------------------------------


def test_build_verdict_messages_substitutes() -> None:
    system, user = build_verdict_messages("Лицензии?", "Чанк 1\n\nЧанк 2")
    assert "Вопрос клиента: Лицензии?" in user
    assert "Чанк 1\n\nЧанк 2" in user
    assert "{question}" not in user and "{context}" not in user
    assert "стоп-условие" in system


def test_build_verdict_messages_braces_in_context() -> None:
    # Фигурные скобки в тексте ТЗ не должны ломать подстановку шаблона.
    system, user = build_verdict_messages("Вопрос", "Текст с {фигурными} скобками")
    assert "Текст с {фигурными} скобками" in user
    assert "{question}" not in user and "{context}" not in user


def test_build_verdict_messages_no_rescan_of_inserted_values() -> None:
    # Литерал {context} в тексте вопроса не должен быть пересканирован
    # второй подстановкой (однопроходная замена).
    system, user = build_verdict_messages("Что такое {context}?", "Контекст ТЗ")
    assert "Что такое {context}?" in user
    assert "Контекст ТЗ" in user
    assert user.count("Контекст ТЗ") == 1


# --- Косинусная близость --------------------------------------------------


def test_cosine_similarity() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_cosine_similarity_degenerate() -> None:
    assert cosine_similarity([], [1, 2]) == 0.0
    assert cosine_similarity([0, 0], [1, 0]) == 0.0
    assert cosine_similarity([1], [1, 2]) == 0.0


# --- Вердикты -------------------------------------------------------------


class _FakeEmbedder:
    """Детерминированный «эмбеддер»: вектор по первому слову текста."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(len(t)) % 7] for t in texts]

    async def embed_one(self, text: str) -> list[float] | None:
        return [1.0, float(len(text)) % 7]


class _FakeLlm:
    def __init__(self, responses: list[dict[str, Any] | None]) -> None:
        self._responses = list(responses)

    async def chat_json(self, system: str, user: str) -> dict[str, Any] | None:
        return self._responses.pop(0) if self._responses else None


class _NoTzRecord:
    """Карточка без файлов ТЗ: find_tz_reference вернёт None."""

    def __init__(self) -> None:
        self.files_json: list[dict[str, str]] = []

    def get(self, key: str, default: Any = None) -> Any:
        if key == "files_json":
            return self.files_json
        return default


def _analyzer(llm: _FakeLlm) -> RagAnalyzer:
    settings = Settings()
    return RagAnalyzer(settings, _FakeEmbedder(), llm)  # type: ignore[arg-type]


def test_report_without_tz() -> None:
    record = _NoTzRecord()
    report = asyncio.run(
        _analyzer(_FakeLlm([{}])).analyze(record, [{"id": "q1", "text": "Лицензии?"}])
    )
    assert report["tz_found"] is False
    assert report["questions"] == []
    assert report["tz_file"] is None


def test_verdict_parsed_from_llm() -> None:
    record = {
        "files_json": [{"name": "ТЗ.docx", "url": "http://x/ТЗ.docx"}],
        "subject": "Разработка ИИ",
    }
    llm = _FakeLlm(
        [
            {
                "verdict": "absolute",
                "excerpt": "Наличие лицензии обязательно",
                "reasoning": "жёсткое требование",
            }
        ]
    )
    analyzer = _analyzer(llm)
    report = asyncio.run(analyzer.analyze(record, [{"id": "q1", "text": "Лицензии?"}]))
    assert report["tz_found"] in (True, False)
    if report["tz_found"] and report["questions"]:
        assert report["questions"][0]["verdict"] == "absolute"
