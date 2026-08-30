"""Unit-тесты RAG-пайплайна (analysis_service.pipeline.rag) и косинусной близости."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from analysis_service.pipeline.matcher import (
    apply_profile_facts,
    resolve_license_code,
)
from analysis_service.pipeline.prompts import (
    build_batch_system_messages,
    build_verdict_messages,
)
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


def test_build_batch_system_messages() -> None:
    system, user = build_batch_system_messages("Секции ТЗ")
    assert "Секции ТЗ" in user
    assert "experience_2571" in user
    assert "minprom_registry" in user
    assert "license_sro" in user
    assert "{context}" not in user
    # Профиль поставщика в промпт не попадает.
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
    report = asyncio.run(
        _analyzer(_FakeLlm([{}])).analyze(record, [{"id": "q1", "text": "Лицензии?"}])
    )
    assert report["tz_found"] is False
    assert report["questions"] == []
    assert report["tz_file"] is None


def test_report_has_cost_and_trace_url() -> None:
    """Отчёт всегда содержит cost (0 при отсутствии вызовов) и trace_url (None без LangFuse)."""
    report = asyncio.run(_analyzer(_FakeLlm([{}])).analyze(_NoTzRecord(), [], {}))
    assert set(report["cost"]) == {"usd"}
    assert report["cost"]["usd"] == 0.0
    assert report["trace_url"] is None


def test_verdict_parsed_from_llm() -> None:
    record = {
        "files_json": [{"name": "ТЗ.docx", "url": "http://x/ТЗ.docx"}],
        "subject": "Разработка ИИ",
    }
    llm = _FakeLlm(
        [
            {
                "experience_2571": {"found": True, "facts": {"required": False}, "excerpt": "—"},
                "minprom_registry": {"found": False, "facts": {}},
                "license_sro": {
                    "found": True,
                    "facts": {"required": True, "license_name": "лицензия МЧС"},
                    "excerpt": "лицензия",
                },
            },
            {
                "verdict": "absolute",
                "excerpt": "Наличие лицензии обязательно",
                "reasoning": "жёсткое требование",
            },
        ]
    )
    analyzer = _analyzer(llm)
    report = asyncio.run(
        analyzer.analyze(
            record,
            [{"id": "q1", "text": "Лицензии?"}],
            {"license_codes": [], "experience_codes": []},
        )
    )
    assert report["tz_found"] in (True, False)
    if report["tz_found"] and report["questions"]:
        q1 = next(q for q in report["questions"] if q["question_id"] == "q1")
        assert q1["verdict"] == "absolute"
        assert q1["source"] == "profile"
        assert q1["marker"] == "🔴"


class _NoneEmbedder:
    """Эмбеддер, недоступный для анализа (возвращает None)."""

    async def embed(self, texts: list[str]) -> None:
        return None

    async def embed_one(self, text: str) -> None:
        return None


def test_analyze_system_llm_failure_unavailable() -> None:
    """Сбой batch-LLM → системные проверки «не проверено» (⚪), а не 🟢."""
    analyzer = _analyzer(_FakeLlm([None]))
    verdicts = asyncio.run(
        analyzer._analyze_system(  # noqa: SLF001
            ["Требуется опыт на площадке; лицензия МЧС; реестр Минпромторга."], {}
        )
    )
    assert len(verdicts) == 3
    assert all(v["verdict"] == "unavailable" for v in verdicts)
    assert all(v["marker"] == "⚪" for v in verdicts)
    assert all(v["source"] == "system" for v in verdicts)


def test_verdict_embed_unavailable() -> None:
    """Недоступен эмбеддер → вопрос профиля «не проверено»."""
    analyzer = _analyzer(_FakeLlm([None]))
    analyzer._embedder = _NoneEmbedder()  # type: ignore[assignment]  # noqa: SLF001
    v = asyncio.run(
        analyzer._verdict_for_question(  # noqa: SLF001
            "q1", "Лицензии?", ["секция ТЗ"], [[1.0, 2.0]]
        )
    )
    assert v["verdict"] == "unavailable"
    assert v["marker"] == "⚪"
    assert v["source"] == "profile"


def test_verdict_llm_unavailable() -> None:
    """LLM-верификация вернула None → вопрос профиля «не проверено»."""
    analyzer = _analyzer(_FakeLlm([None]))
    v = asyncio.run(
        analyzer._verdict_for_question(  # noqa: SLF001
            "q1", "Лицензии?", ["секция ТЗ"], [[1.0, 2.0]]
        )
    )
    assert v["verdict"] == "unavailable"
    assert v["marker"] == "⚪"


def test_report_status() -> None:
    """Верхнеуровневый статус rag_report (ok/deferred/error/no_tz)."""
    status = RagAnalyzer._status
    assert status(False, None, []) == "no_tz"
    assert status(True, None, []) == "ok"
    assert status(True, "ошибка", []) == "error"
    assert status(True, None, [{"verdict": "unavailable"}]) == "deferred"
    assert status(True, None, [{"verdict": "soft"}, {"verdict": "no_stop_condition"}]) == "ok"


# --- Лексический ретривал системных проверок ------------------------------


def test_select_relevant_chunks() -> None:
    from analysis_service.pipeline.system_questions import SYSTEM_RETRIEVAL_PATTERNS

    analyzer = _analyzer(_FakeLlm([]))
    chunks = [
        "Общие положения о проведении закупки",
        "Требование к опыту: подтверждение на электронной площадке по ПП РФ 2571",
        "Требования к продукции: реестр Минпромторга, пометка «не установлено»",
        "Требуется лицензия МЧС на монтаж пожарной сигнализации",
        "Срок исполнения контракта — 90 дней",
    ]
    assert analyzer._select_relevant_chunks(  # noqa: SLF001
        SYSTEM_RETRIEVAL_PATTERNS["sys:exp_2571"], chunks
    ) == [1]
    assert analyzer._select_relevant_chunks(  # noqa: SLF001
        SYSTEM_RETRIEVAL_PATTERNS["sys:minprom_registry"], chunks
    ) == [2]
    assert analyzer._select_relevant_chunks(  # noqa: SLF001
        SYSTEM_RETRIEVAL_PATTERNS["sys:license_sro"], chunks
    ) == [3]


def test_analyze_system_batch_and_matcher() -> None:
    chunks = [
        "Требуется опыт исполнения контрактов, подтверждённый на электронной площадке.",
        "Запрет иностранной продукции; реестр Минпромторга — не установлено.",
        "Требуется лицензия МЧС на монтаж пожарной сигнализации.",
    ]
    batch_response = {
        "experience_2571": {
            "found": True,
            "facts": {"required": True, "confirmation": "platform", "ref_2571": True},
            "excerpt": "подтверждение на электронной площадке",
            "reasoning": "ПП РФ 2571",
        },
        "minprom_registry": {
            "found": True,
            "facts": {"required": False, "not_established_note": True, "foreign_goods_ban": True},
            "excerpt": "не установлено",
            "reasoning": "пометка",
        },
        "license_sro": {
            "found": True,
            "facts": {"required": True, "license_name": "лицензия МЧС", "authority": "МЧС России"},
            "excerpt": "лицензия МЧС",
            "reasoning": "монтаж пожарной сигнализации",
        },
    }
    analyzer = _analyzer(_FakeLlm([batch_response]))
    verdicts = asyncio.run(
        analyzer._analyze_system(chunks, {"license_codes": [], "experience_codes": []})  # noqa: SLF001
    )
    by_id = {v["question_id"]: v for v in verdicts}
    assert by_id["sys:exp_2571"]["verdict"] == "absolute"
    assert by_id["sys:exp_2571"]["marker"] == "🔴"
    assert by_id["sys:exp_2571"]["source"] == "system"
    assert by_id["sys:minprom_registry"]["verdict"] == "no_stop_condition"
    assert by_id["sys:minprom_registry"]["marker"] == "🟢"
    assert by_id["sys:license_sro"]["verdict"] == "absolute"
    assert by_id["sys:license_sro"]["marker"] == "🔴"

    # Те же факты, но у компании есть опыт на площадке и лицензия МЧС.
    analyzer_ok = _analyzer(_FakeLlm([batch_response]))
    verdicts_ok = asyncio.run(
        analyzer_ok._analyze_system(  # noqa: SLF001
            chunks,
            {"license_codes": ["mchs"], "experience_codes": ["platform"]},
        )
    )
    by_id_ok = {v["question_id"]: v for v in verdicts_ok}
    assert by_id_ok["sys:exp_2571"]["verdict"] == "no_stop_condition"
    assert by_id_ok["sys:license_sro"]["verdict"] == "no_stop_condition"


def test_analyze_system_no_llm_when_no_sections() -> None:
    """Если в ТЗ нет стандартных секций стоп-условий — LLM не вызывается."""
    llm = _FakeLlm([])
    analyzer = _analyzer(llm)
    verdicts = asyncio.run(
        analyzer._analyze_system(  # noqa: SLF001
            ["Только общие положения и условия оплаты."], {}
        )
    )
    assert len(verdicts) == 3
    assert all(v["verdict"] == "no_stop_condition" for v in verdicts)
    assert all(v["source"] == "system" for v in verdicts)


# --- Stage B: матчер фактов ТЗ × фактов профиля -----------------------------


def _batch(
    exp: dict[str, Any] | None = None,
    mp: dict[str, Any] | None = None,
    lic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"experience_2571": exp, "minprom_registry": mp, "license_sro": lic}


def test_matcher_experience_rules() -> None:
    empty = {"license_codes": [], "experience_codes": []}
    platform_ok = {"license_codes": [], "experience_codes": ["platform"]}

    v = apply_profile_facts(
        _batch(exp={"found": True, "facts": {"required": True, "confirmation": "platform"}}), empty
    )
    assert v[0]["verdict"] == "absolute" and v[0]["marker"] == "🔴"
    v = apply_profile_facts(
        _batch(exp={"found": True, "facts": {"required": True, "confirmation": "platform"}}),
        platform_ok,
    )
    assert v[0]["verdict"] == "no_stop_condition"
    v = apply_profile_facts(
        _batch(exp={"found": True, "facts": {"required": True, "confirmation": "evaluation_only"}}),
        empty,
    )
    assert v[0]["verdict"] == "no_stop_condition"
    v = apply_profile_facts(
        _batch(exp={"found": True, "facts": {"required": True, "confirmation": "documents"}}), empty
    )
    assert v[0]["verdict"] == "soft" and v[0]["marker"] == "🟡"
    v = apply_profile_facts(_batch(exp={"found": True, "facts": {"required": False}}), empty)
    assert v[0]["verdict"] == "no_stop_condition"
    # Сбой/нет данных по проверке не должен давать ложный барьер.
    v = apply_profile_facts(_batch(exp=None), empty)
    assert v[0]["verdict"] == "no_stop_condition"


def test_matcher_minprom_rules() -> None:
    empty = {"license_codes": [], "experience_codes": []}
    v = apply_profile_facts(_batch(mp={"found": True, "facts": {"required": True}}), empty)
    assert v[1]["verdict"] == "absolute" and v[1]["marker"] == "🔴"
    v = apply_profile_facts(
        _batch(mp={"found": True, "facts": {"required": False, "not_established_note": True}}),
        empty,
    )
    assert v[1]["verdict"] == "no_stop_condition" and v[1]["marker"] == "🟢"


def test_matcher_license_rules() -> None:
    no_license = {"license_codes": [], "experience_codes": []}
    has_mchs = {"license_codes": ["mchs"], "experience_codes": []}

    lic = {
        "found": True,
        "facts": {"required": True, "license_name": "лицензия МЧС на монтаж пожарной сигнализации"},
    }
    v = apply_profile_facts(_batch(lic=lic), no_license)
    assert v[2]["verdict"] == "absolute" and v[2]["marker"] == "🔴"
    v = apply_profile_facts(_batch(lic=lic), has_mchs)
    assert v[2]["verdict"] == "no_stop_condition" and v[2]["marker"] == "🟢"

    # Нераспознанный вид лицензии — мягкий маркер, не отсеивает закупку.
    odd = {
        "found": True,
        "facts": {"required": True, "license_name": "какое-то экзотическое разрешение"},
    }
    v = apply_profile_facts(_batch(lic=odd), no_license)
    assert v[2]["verdict"] == "soft" and v[2]["marker"] == "🟡"

    # Нет требования — нет барьера.
    v = apply_profile_facts(_batch(lic={"found": True, "facts": {"required": False}}), no_license)
    assert v[2]["verdict"] == "no_stop_condition"


def test_resolve_license_code_aliases() -> None:
    assert resolve_license_code({"license_code": "mchs"}) == "mchs"
    assert (
        resolve_license_code(
            {"license_name": "Лицензия МЧС России на монтаж пожарной сигнализации"}
        )
        == "mchs"
    )
    assert resolve_license_code({"authority": "ФСБ России (криптографические средства)"}) == "fsb"
    assert (
        resolve_license_code({"license_name": "Лицензия ФСБ России на работы с гостайной"})
        == "fsb_gostayna"
    )
    assert (
        resolve_license_code(
            {
                "license_name": "лицензия УФСБ на работы с государственной тайной",
                "authority": "УФСБ",
                "reasoning": "степень секретности не ниже «совершенно секретно»",
            }
        )
        == "fsb_gostayna"
    )
    assert (
        resolve_license_code({"license_name": "Лицензия на образовательную деятельность"})
        == "education"
    )
    assert resolve_license_code({"license_name": "непонятное разрешение"}) is None
    assert resolve_license_code({"license_code": "other"}) is None


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
    report = asyncio.run(_analyzer(_FakeLlm([])).analyze(record, [], {}))
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
    report = asyncio.run(_analyzer(_FakeLlm([])).analyze(record, [], {}))
    assert report["tz_found"] is True
    assert report["tz_file"] == "ТЗ.docx"
