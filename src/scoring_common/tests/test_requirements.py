"""Unit-тесты извлечения/классификации требований к участнику (scoring_common.requirements)."""

from __future__ import annotations

import pytest

from scoring_common.requirements import (
    _classify_section,
    build_structure,
    enumerate_document_refs,
    extract_requirements,
    split_sections,
)
from scoring_common.tz.files import FileRef


def test_matches_requirement_in_names() -> None:
    from scoring_common.requirements import _matches_requirement, _normalize_name

    assert _matches_requirement("требования к участникам закупки")
    assert _matches_requirement("требование к участнику")
    assert _matches_requirement("Требования к исполнителю")
    assert _matches_requirement("Требования к составу заявки")
    # Имена файлов с разделителями-подчёркиваниями нормализуются к пробелу.
    assert _matches_requirement(_normalize_name("Требования_к_участникам.docx"))
    assert _matches_requirement("требования к составу заявки и инструкция")
    assert not _matches_requirement("техническое задание")
    assert not _matches_requirement("описание объекта закупки")
    assert not _matches_requirement("проект договора")


def test_matches_requirement_with_predyavlyaemye_pattern() -> None:
    from scoring_common.requirements import _matches_requirement

    assert _matches_requirement("Требования, предъявляемые к участнику закупки")
    assert _matches_requirement("Требования предъявляемые к участнику закупки")
    assert _matches_requirement("требования, предъявляемые к участникам закупки")
    assert _matches_requirement(
        "Требования, предъявляемые к участнику закупки, изложены в разделе 3"
    )
    assert not _matches_requirement("Требования предъявляемые исполнителю закупки")


def test_matches_requirement_strict_word_order() -> None:
    """Между указанными словами не должно быть других слов (только пробелы)."""
    from scoring_common.requirements import _matches_requirement

    assert _matches_requirement("Требования к участнику")
    # Дополнительное слово между «к» и «участнику» ломает шаблон.
    assert not _matches_requirement("Требования в части к участнику")
    # Дополнительное слово между «требования» и «к».
    assert not _matches_requirement("Требования закупки к участнику")
    # Обратный порядок слов.
    assert not _matches_requirement("Участнику к требования")
    # У «предъявляемые» и «к» не должно быть посторонних слов.
    assert not _matches_requirement("Требования, предъявляемые в части к участнику закупки")


def test_split_sections_markdown_headings() -> None:
    md = (
        "# Общие положения\n\nТекст общих положений.\n\n"
        "# Требования к участнику\n\nТребуется лицензия МЧС.\n\n"
        "## Документы\n\nСписок документов."
    )
    sections = split_sections(md)
    # Первый «раздел» (до первого заголовка) не обязан начинаться с заголовка.
    headings = [s["heading"] for s in sections if s["heading"]]
    assert any("Требования к участнику" in h for h in headings)
    assert any("Общие положения" in h for h in headings)


def test_split_sections_without_headings_single_section() -> None:
    sections = split_sections("Просто сплошной текст без заголовков.")
    assert len(sections) == 1
    assert "Просто сплошной текст" in sections[0]["text"]


def test_enumerate_document_refs_skips_archives_and_expands(monkeypatch) -> None:
    import scoring_common.requirements as req_mod

    # Прямые файлы + записи архивов.
    inner_names = lambda url, *a, **k: ["doc/ТЗ.txt", "x.docx"]  # noqa: E731
    monkeypatch.setattr(req_mod, "_archive_inner_names", inner_names)
    record = {
        "files_json": [
            {"name": "Требования к участникам.pdf", "url": "http://x/req.pdf"},
            {"name": "docs.zip", "url": "http://x/docs.zip"},
        ]
    }
    refs = enumerate_document_refs(record)
    names = [r.name for r in refs]
    assert "Требования к участникам.pdf" in names
    assert "doc/ТЗ.txt" in names
    assert "x.docx" in names
    assert all("#" in r.url for r in refs if r.name != "Требования к участникам.pdf")


def test_build_structure_empty() -> None:
    assert build_structure([]) == {}


def test_build_structure_groups_main_and_other() -> None:
    candidates = [
        {"source": "a.pdf", "text": "Требуется лицензия МЧС на монтаж."},
        {"source": "b.pdf", "text": "Подтверждённый опыт исполнения за 3 года."},
        {"source": "c.pdf", "text": "Выписка из реестра Минпромторга обязательна."},
        {"source": "d.pdf", "text": "В состав заявки входят паспорт и смета."},
    ]
    structure = build_structure(candidates)
    assert structure["licenses"][0]["text"].startswith("Требуется лицензия МЧС")
    assert structure["licenses"][0]["data"] is None
    assert structure["licenses"][0]["file_name"] == "a.pdf"
    assert structure["experience"][0]["data"] is None
    assert structure["minprom"][0]["data"] is None
    assert len(structure["other"]) == 1
    assert structure["other"][0]["data"] is None
    assert structure["other"][0]["file_name"] == "d.pdf"
    # Разделы одного типа НЕ сливаются: каждый — отдельный элемент списка.
    assert structure["licenses"] == [
        {"text": "Требуется лицензия МЧС на монтаж.", "data": None, "file_name": "a.pdf"}
    ]


def test_build_structure_file_names_list_when_multiple_sources() -> None:
    candidates = [
        {"source": "a.pdf", "text": "Требуется лицензия МЧС."},
        {"source": "b.docx", "text": "Нужно членство в СРО."},
    ]
    structure = build_structure(candidates)
    assert structure["licenses"] == [
        {"text": "Требуется лицензия МЧС.", "data": None, "file_name": "a.pdf"},
        {"text": "Нужно членство в СРО.", "data": None, "file_name": "b.docx"},
    ]


def test_classify_section() -> None:
    assert _classify_section("Требуется лицензия МЧС и членство в СРО.") == "licenses"
    assert _classify_section("Подтверждённый опыт исполнения контрактов.") == "experience"
    assert _classify_section("Запрет иностранной продукции, реестр Минпромторга.") == "minprom"
    assert _classify_section("В состав заявки входят документы.") == "other"


def test_replace_marker_values() -> None:
    from scoring_common.requirements import _replace_marker_values

    assert (
        _replace_marker_values("Дополнительные требования. Не установлено")
        == "Дополнительные требования. НЕТ"
    )
    assert _replace_marker_values("Лот № 1: Не требуется") == "Лот № 1: НЕТ"
    assert _replace_marker_values("Не предоставляются") == "НЕТ"
    assert _replace_marker_values("Не применяется") == "НЕТ"


def test_requirement_section_rows_split_by_single_cell_header() -> None:
    from scoring_common.requirements import _requirement_section_rows

    md = (
        "| I. Информация о проведении закупки |  |  |\n"
        "| --- | --- | --- |\n"
        "| 1 | Наименование объекта закупки |  |\n\n"
        "| II. Требования, предъявляемые к участникам закупки |  |  |\n"
        "| --- | --- | --- |\n"
        "| 10 | Общие требования |  |\n"
        "| 13 | Требования к участникам закупок аудиторских услуг. | Не установлено |\n\n"
        "| III. Размер и порядок обеспечения заявок |  |  |\n"
        "| --- | --- | --- |\n"
        "| 21 | Обеспечение |  |\n"
    )
    rows = _requirement_section_rows(md)
    assert [r[0] for r in rows] == ["10", "13"]
    assert rows[1][2] == "Не установлено"


def test_build_structure_includes_additional() -> None:
    structure = build_structure(
        [{"source": "req.pdf", "text": "Отсутствие ограничений.", "additional": "НЕТ"}]
    )
    assert structure["other"][0]["additional"] == "НЕТ"


def test_replaces_markers_in_plain_text_candidate() -> None:
    # Маркеры отрицания учитываются и в плоском (не табличном) требовании.
    structure = build_structure(
        [{"source": "req.pdf", "text": "13 Доп. требования к аудиторским услугам.\nНе установлено"}]
    )
    texts = [it["text"] for items in structure.values() for it in items]
    assert any("НЕТ" in t for t in texts)


def test_extract_requirements_plain_text_marker_to_net(_fake_extract_text: dict[str, str]) -> None:
    _fake_extract_text["req.pdf"] = (
        "# Требования к участнику\n\n"
        "Дополнительные требования к участникам закупок аудиторских услуг. Не установлено"
    )
    record = {"files_json": [{"name": "req.pdf", "url": "http://x/req.pdf"}]}
    structure = extract_requirements(record)
    texts = [it["text"] for items in structure.values() for it in items]
    assert any("НЕТ" in t for t in texts)


def test_extract_requirements_from_table_rows(monkeypatch) -> None:
    import scoring_common.requirements as req_mod

    md = (
        "| II. Требования, предъявляемые к участникам закупки |  |  |\n"
        "| --- | --- | --- |\n"
        "| 13 | Требования к участникам закупок аудиторских услуг. | Не установлено |\n"
    )
    monkeypatch.setattr(req_mod, "_download", lambda *a, **k: b"pdf")
    monkeypatch.setattr(req_mod, "pdf_to_markdown_tables", lambda raw: md)
    record = {"files_json": [{"name": "req.pdf", "url": "http://x/req.pdf"}]}
    structure = extract_requirements(record)
    assert "other" in structure
    item = structure["other"][0]
    assert "аудиторских" in item["text"]
    # маркер-отрицание → флаг negated, без дублирующего additional="НЕТ".
    assert item["negated"] is True
    assert "additional" not in item
    assert item["file_name"] == "req.pdf"


@pytest.fixture
def _fake_extract_text(monkeypatch) -> None:
    """Подмена извлечения текста: возвращает текст по имени файла (без сети)."""

    docs: dict[str, str] = {}

    def fake(ref: FileRef, timeout: float = 30.0, verify_ssl: bool = True) -> str | None:
        return docs.get(ref.name)

    monkeypatch.setattr("scoring_common.requirements.extract_text", fake)
    # Table-путь не должен ходить в сеть в юнит-тестах.
    monkeypatch.setattr("scoring_common.requirements._download", lambda *a, **k: None)
    return docs


def test_extract_requirements_by_heading(_fake_extract_text: dict[str, str]) -> None:
    _fake_extract_text["req.pdf"] = (
        "# Требования к участнику\n\nТребуется лицензия МЧС. Не установлено.\n\n"
        "# Общие положения\n\nСрок исполнения 90 дней."
    )
    record = {"files_json": [{"name": "req.pdf", "url": "http://x/req.pdf"}]}
    structure = extract_requirements(record)
    assert "licenses" in structure
    assert "Требуется лицензия МЧС" in structure["licenses"][0]["text"]
    assert structure["licenses"][0]["file_name"] == "req.pdf"


def test_extract_requirements_name_match_whole_doc(_fake_extract_text: dict[str, str]) -> None:
    # Имя файла матчит шаблон → весь документ считается разделом требований.
    _fake_extract_text["Требования к участникам.docx"] = (
        "Требуется лицензия МЧС. Не установлено.\n\nПодтверждённый опыт за 3 года."
    )
    record = {"files_json": [{"name": "Требования к участникам.docx", "url": "http://x/req.docx"}]}
    structure = extract_requirements(record)
    assert "licenses" in structure
    assert "Требуется лицензия МЧС" in structure["licenses"][0]["text"]
    assert structure["licenses"][0]["file_name"] == "Требования к участникам.docx"


def test_extract_requirements_fallback_to_doc_text(_fake_extract_text: dict[str, str]) -> None:
    # Заголовок не матчит, но текст раздела содержит шаблон — фолбэк по тексту.
    _fake_extract_text["doc.pdf"] = (
        "Порядок оказания услуг.\n\nТребования к участнику изложены. Не установлено."
    )
    record = {"files_json": [{"name": "doc.pdf", "url": "http://x/doc.pdf"}]}
    structure = extract_requirements(record)
    assert structure  # не {}


def test_extract_requirements_empty_when_no_patterns(_fake_extract_text: dict[str, str]) -> None:
    _fake_extract_text["tz.pdf"] = "Описание предмета закупки и условия оплаты."
    record = {"files_json": [{"name": "tz.pdf", "url": "http://x/tz.pdf"}]}
    assert extract_requirements(record) == {}
