"""Unit-тесты RAG-пайплайна (analysis_service.pipeline.rag) и косинусной близости."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from analysis_service.pipeline.matcher import (
    build_license_summary,
    license_kinds_in_text,
    requirement_category_status,
    resolve_license_kind,
)
from analysis_service.pipeline.prompts import build_requirements_data_messages
from analysis_service.pipeline.rag import RagAnalyzer
from analysis_service.settings import Settings

from scoring_common.conditions import extraction_key, normalize_report_fields
from scoring_common.embeddings import cosine_similarity

# --- Промпты ------------------------------------------------------------


def test_build_requirements_data_messages() -> None:
    system, user = build_requirements_data_messages("licenses", "Требуется лицензия МЧС.")
    assert "Требуется лицензия МЧС." in user
    assert "{kind}" not in user and "{text}" not in user and "{structure}" not in user
    # Контракт схемы лицензий присутствует в промпте.
    assert "kinds" in user
    assert "профил" not in system.lower()


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

    def reset_cost(self) -> None:
        pass

    @property
    def total_cost_usd(self) -> float:
        return 0.0


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
    report = asyncio.run(_analyzer(_FakeLlm([{}])).analyze(record))
    assert report["tz_found"] is False
    assert report["fields"] == []
    assert report["tz_file"] is None


def test_report_has_cost_and_trace_url() -> None:
    """Отчёт всегда содержит cost (0 при отсутствии вызовов) и trace_url (None без LangFuse)."""
    report = asyncio.run(_analyzer(_FakeLlm([{}])).analyze(_NoTzRecord(), metadata={}))
    cost = report["cost"]
    assert cost["usd"] == 0.0
    # Стандартизованные метрики стадии всегда присутствуют.
    for key in (
        "usd",
        "tokens",
        "cost_details",
        "models",
        "calls",
        "latency_ms",
        "duration_ms",
        "delay_ms",
    ):
        assert key in cost
    assert report["trace_url"] is None


class _NoneEmbedder:
    """Эмбеддер, недоступный для анализа (возвращает None)."""

    async def embed(self, texts: list[str]) -> None:
        return None

    async def embed_one(self, text: str) -> None:
        return None


def test_report_embed_unavailable_gives_deferred_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступен эмбеддер → отчётные поля не оценены, статус «deferred»."""
    from analysis_service.pipeline import rag as rag_mod

    from scoring_common.tz.files import FileRef

    tz_ref = FileRef("ТЗ.docx", "http://x/ТЗ.docx")

    def fake_enumerate(rec: dict, timeout: float = 30.0, verify_ssl: bool = True) -> list[FileRef]:
        return [tz_ref]

    def fake_extract(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str:
        return "Общее описание работ."

    monkeypatch.setattr(rag_mod, "enumerate_document_refs", fake_enumerate)
    monkeypatch.setattr(rag_mod, "extract_text_cached", fake_extract)

    analyzer = _analyzer(_FakeLlm([None]))
    analyzer._embedder = _NoneEmbedder()  # type: ignore[assignment]  # noqa: SLF001
    record = {"files_json": [{"name": "ТЗ.docx", "url": "http://x/ТЗ.docx"}]}
    report = asyncio.run(analyzer.analyze(record, report_fields=[{"id": "f1", "name": "объём"}]))
    assert report["status"] == "deferred"
    assert report["fields"] == []


def test_report_status() -> None:
    """Верхнеуровневый статус rag_report (ok/error/no_tz)."""
    status = RagAnalyzer._status
    assert status(False, None) == "no_tz"
    assert status(True, None) == "ok"
    assert status(True, "ошибка") == "error"


# --- Заполнение data требований к участнику (LLM-этап) ---------------------


def test_requirements_data_fill() -> None:
    structure = {
        "licenses": [
            {"text": "Требуется лицензия МЧС на монтаж.", "data": None, "file_name": "req.pdf"}
        ],
        "experience": [
            {"text": "Подтверждённый опыт за 3 года.", "data": None, "file_name": "req.pdf"}
        ],
        "minprom": [
            {"text": "Выписка из реестра Минпромторга.", "data": None, "file_name": "req.pdf"}
        ],
        "other": [
            {"text": "Состав заявки: паспорт, смета.", "data": None, "file_name": "docs.pdf"}
        ],
    }
    llm = _FakeLlm(
        [
            {"required": True, "kinds": [{"type": "license", "name": "МЧС", "mandatory": True}]},
            {"required": True, "confirmation": "documents", "min_contracts": 1, "ref_2571": False},
            {"required": True, "foreign_goods_ban": True, "not_established_note": False},
            {"type": "состав заявки", "summary": "паспорт, смета", "conditions": []},
        ]
    )
    analyzer = _analyzer(llm)
    filled = asyncio.run(analyzer.fill_requirements_data(structure))
    assert filled["licenses"][0]["data"]["kinds"][0]["name"] == "МЧС"
    assert filled["licenses"][0]["file_name"] == "req.pdf"
    assert filled["experience"][0]["data"]["confirmation"] == "documents"
    assert filled["minprom"][0]["data"]["foreign_goods_ban"] is True
    assert filled["other"][0]["data"]["type"] == "состав заявки"
    assert filled["other"][0]["file_name"] == "docs.pdf"
    # Уже заполненные data не пересчитываются (идемпотентность).
    assert asyncio.run(analyzer.fill_requirements_data(filled)) == filled


def test_requirements_data_fill_llm_failure_keeps_none() -> None:
    # Сбой LLM (None) → data остаётся None, структура сохраняется.
    structure = {
        "licenses": [{"text": "Требуется лицензия МЧС.", "data": None, "file_name": "req.pdf"}],
        "other": [{"text": "Состав заявки.", "data": None, "file_name": "docs.pdf"}],
    }
    analyzer = _analyzer(_FakeLlm([None, None]))
    filled = asyncio.run(analyzer.fill_requirements_data(structure))
    assert filled["licenses"][0]["data"] is None
    assert filled["licenses"][0]["file_name"] == "req.pdf"
    assert filled["other"][0]["data"] is None
    assert filled["other"][0]["file_name"] == "docs.pdf"


def test_resolve_license_kind_aliases() -> None:
    assert resolve_license_kind({"license_code": "mchs"}) == "mchs"
    assert (
        resolve_license_kind({"license_name": "Лицензия МЧС на монтаж пожарной сигнализации"})
        == "mchs"
    )
    assert resolve_license_kind({"authority": "ФСБ (криптографические средства)"}) == "fsb"
    assert (
        resolve_license_kind({"license_name": "Лицензия ФСБ на работы с гостайной"})
        == "fsb_gostayna"
    )
    assert (
        resolve_license_kind(
            {
                "license_name": "лицензия УФСБ на работы с государственной тайной",
                "authority": "УФСБ",
                "reasoning": "степень секретности не ниже «совершенно секретно»",
            }
        )
        == "fsb_gostayna"
    )
    assert (
        resolve_license_kind({"license_name": "Лицензия на образовательную деятельность"})
        == "education"
    )
    assert resolve_license_kind({"license_name": "непонятное разрешение"}) is None
    assert resolve_license_kind({"license_code": "other"}) is None


# --- Детектор обязанностей Исполнителя и фолбэк на «Описание» ---------------


def test_has_executor_duties_matches_obligations() -> None:
    from scoring_common.tz import _has_executor_duties

    assert _has_executor_duties("Исполнитель обязан предоставить отчёт.")
    assert _has_executor_duties("Подрядчик должен выполнить работы в срок.")
    assert _has_executor_duties("Участник несёт ответственность за качество.")
    assert _has_executor_duties("Требования к Исполнителю изложены в разделе 3.")
    assert _has_executor_duties("Подрядчик обязан соблюдать требования техзадания.")


def test_has_executor_duties_rejects_no_duties() -> None:
    from scoring_common.tz import _has_executor_duties

    assert not _has_executor_duties("Описание предмета закупки и порядок оплаты.")
    assert not _has_executor_duties("должностной регламент не входит в предмет.")
    assert not _has_executor_duties("Срок исполнения контракта — 90 дней.")
    assert not _has_executor_duties("")


def test_analyze_falls_back_to_description_when_tz_has_no_duties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from analysis_service.pipeline import rag as rag_mod

    from scoring_common.tz.files import FileRef

    desc_ref = FileRef("Описание.docx", "http://x/Описание.docx")

    def fake_resolve(
        rec: dict, timeout: float = 30.0, verify_ssl: bool = True
    ) -> tuple[FileRef, str]:
        return (desc_ref, "Исполнитель обязан предоставить отчёт о выполнении работ.")

    monkeypatch.setattr(rag_mod, "resolve_tz_content", fake_resolve)
    record = {"files_json": [{"name": "ТЗ.docx", "url": "http://x/ТЗ.docx"}]}
    report = asyncio.run(_analyzer(_FakeLlm([])).analyze(record, metadata={}))
    assert report["tz_found"] is True
    assert report["tz_file"] == "Описание.docx"


def test_analyze_keeps_tz_when_duties_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from analysis_service.pipeline import rag as rag_mod

    from scoring_common.tz.files import FileRef

    tz_ref = FileRef("ТЗ.docx", "http://x/ТЗ.docx")

    def fake_resolve(
        rec: dict, timeout: float = 30.0, verify_ssl: bool = True
    ) -> tuple[FileRef, str]:
        return (tz_ref, "Исполнитель обязан предоставить отчёт о выполнении.")

    monkeypatch.setattr(rag_mod, "resolve_tz_content", fake_resolve)
    record = {"files_json": [{"name": "ТЗ.docx", "url": "http://x/ТЗ.docx"}]}
    report = asyncio.run(_analyzer(_FakeLlm([])).analyze(record, metadata={}))
    assert report["tz_found"] is True
    assert report["tz_file"] == "ТЗ.docx"


# --- Регрессия: ответ по вопросу может лежать НЕ в файле ТЗ -----------------


def test_collect_document_chunks_covers_all_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Чанки собираются со ВСЕХ документов закупки, а не только с файла,
    определённого как ТЗ — требования к участнику часто лежат в других
    приложениях (проект контракта, извещение и т.п.), не в самом ТЗ."""
    from analysis_service.pipeline import rag as rag_mod

    from scoring_common.tz.files import FileRef

    tz_ref = FileRef("ТЗ.docx", "http://x/ТЗ.docx")
    contract_ref = FileRef("Проект контракта.docx", "http://x/Контракт.docx")

    def fake_enumerate(rec: dict, timeout: float = 30.0, verify_ssl: bool = True) -> list[FileRef]:
        return [tz_ref, contract_ref]

    def fake_extract(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str:
        if ref.name == "ТЗ.docx":
            return "Общее описание работ по установке оборудования."
        return "Исполнитель вправе привлекать соисполнителей без ограничения по объёму."

    monkeypatch.setattr(rag_mod, "enumerate_document_refs", fake_enumerate)
    monkeypatch.setattr(rag_mod, "extract_text_cached", fake_extract)

    analyzer = _analyzer(_FakeLlm([]))
    chunks, sources = asyncio.run(
        analyzer._collect_document_chunks({"files_json": []})  # noqa: SLF001
    )
    assert any("соисполнителей" in c for c in chunks)
    assert set(sources) == {"ТЗ.docx", "Проект контракта.docx"}


# --- Отчётные поля (FR-12.2): проводка через RagAnalyzer.analyze -----------


def test_analyze_no_tz_includes_empty_fields() -> None:
    """Нет ни одного документа → fields=[], без вызова LLM/эмбеддера."""
    report = asyncio.run(
        _analyzer(_FakeLlm([])).analyze(
            _NoTzRecord(), report_fields=[{"id": "f1", "name": "объём"}]
        )
    )
    assert report["fields"] == []


def test_analyze_includes_report_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """rag_report несёт fields, переиспользуя уже собранные чанки документов
    (та же _collect_document_chunks)."""
    from analysis_service.pipeline import rag as rag_mod

    from scoring_common.tz.files import FileRef

    tz_ref = FileRef("ТЗ.docx", "http://x/ТЗ.docx")

    def fake_enumerate(rec: dict, timeout: float = 30.0, verify_ssl: bool = True) -> list[FileRef]:
        return [tz_ref]

    def fake_extract(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str:
        return "Объём партии отходов составляет 4000 м3."

    monkeypatch.setattr(rag_mod, "enumerate_document_refs", fake_enumerate)
    monkeypatch.setattr(rag_mod, "extract_text_cached", fake_extract)

    llm = _FakeLlm([{"found": True, "value": 4000, "confidence": "high", "excerpt": "4000 м3"}])
    analyzer = _analyzer(llm)
    record = {"files_json": [{"name": "ТЗ.docx", "url": "http://x/ТЗ.docx"}]}
    report = asyncio.run(
        analyzer.analyze(
            record,
            report_fields=normalize_report_fields(
                [{"id": "f1", "name": "объём партии", "type": "number"}]
            ),
        )
    )
    assert report["fields"] == [
        {
            "field_id": "f1",
            "field_name": "объём партии",
            "field_type": "number",
            "unit": None,
            "found": True,
            "value": 4000.0,
            "confidence": "high",
            "excerpt": "4000 м3",
            "source_file": "ТЗ.docx",
            "reasoning": "",
            "extraction_key": extraction_key(
                normalize_report_fields([{"id": "f1", "name": "объём партии", "type": "number"}])[0]
            ),
            "condition": None,
            "match": None,
            "check_status": "no_condition",
            "mismatched_values": [],
            "llm_match": None,
            "severity": None,
            "value_sources": None,
            "unconfirmed_values": [],
            "rejected_values": [],
            "tz_windows": {},
            "mismatch_reasons": {},
            "requirements": {},
            "near_matches": {},
            "near_labels": {},
            "source_status": None,
        }
    ]


# --- Сводка по лицензиям и статус категорий требований (отчёт карточки) -----


def test_license_kinds_in_text_detects_and_dedupes_gostayna() -> None:
    kinds = license_kinds_in_text(
        "Требуется лицензия ФСБ на работы с государственной тайной (степень секретности)."
    )
    # Гостайна — частный случай ФСБ: общий «фсб» не добавляется отдельно.
    assert kinds == ["fsb_gostayna"]
    assert "mchs" in license_kinds_in_text("Нужна лицензия МЧС на монтаж пожарной сигнализации.")


def test_build_license_summary_from_llm_data() -> None:
    requirements = {
        "licenses": [
            {
                "text": "Требуется лицензия ...",
                "data": {
                    "required": True,
                    "kinds": [
                        {
                            "type": "license",
                            "name": "Лицензия МЧС",
                            "code": "mchs",
                            "mandatory": True,
                        }
                    ],
                },
            }
        ]
    }
    without = build_license_summary(requirements, [])
    assert without["found"] is True and without["required"] is True
    assert [i["kind"] for i in without["items"]] == ["mchs"]
    assert without["items"][0]["available"] is False

    with_mchs = build_license_summary(
        requirements,
        ["деятельность по монтажу и ремонту средств обеспечения пожарной безопасности"],
    )
    assert with_mchs["items"][0]["available"] is True


def test_build_license_summary_deterministic_fallback() -> None:
    # data нет (аккаунт без LLM) — вид определяется лексически по тексту.
    requirements = {
        "licenses": [{"text": "Требуется лицензия ФСТЭК на техническую защиту информации."}]
    }
    summary = build_license_summary(requirements, [])
    assert summary["required"] is True
    assert [i["kind"] for i in summary["items"]] == ["fstek"]
    assert summary["items"][0]["available"] is False


def test_build_license_summary_negated_is_not_required() -> None:
    # Пометка «не установлено/не требуется» (negated) → требования нет.
    summary = build_license_summary({"licenses": [{"text": "Лицензии НЕТ", "negated": True}]}, [])
    assert summary["found"] is True
    assert summary["required"] is False and summary["negated"] is True
    assert summary["items"] == []


def test_build_license_summary_empty() -> None:
    summary = build_license_summary({}, [])
    assert summary == {"found": False, "required": False, "negated": False, "items": []}


def test_requirement_category_status() -> None:
    assert requirement_category_status({}, "experience") == {
        "found": False,
        "required": False,
        "negated": False,
    }
    assert requirement_category_status(
        {"experience": [{"text": "опыт НЕТ", "negated": True}]}, "experience"
    ) == {"found": True, "required": False, "negated": True}
    assert requirement_category_status({"minprom": [{"text": "требуется выписка"}]}, "minprom") == {
        "found": True,
        "required": True,
        "negated": False,
    }
