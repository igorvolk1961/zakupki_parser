"""Unit-тесты чанкинга ТЗ (analysis_service.pipeline.chunker)."""

from __future__ import annotations

from analysis_service.pipeline.chunker import split_tz_sections


def test_short_tz_is_one_chunk() -> None:
    text = "Разработка системы автоматизации\nОбщие положения.\nПредмет: автоматизация."
    chunks = split_tz_sections(text)
    # «Общие положения.» — заголовок раздела: чанк не пересекает границу.
    assert chunks == [
        "Разработка системы автоматизации",
        "Общие положения.\n\nПредмет: автоматизация.",
    ]


def test_sections_split_by_heading() -> None:
    text = (
        "Поставка систем автоматизации\n\n"
        "1. Общие положения\nТекст первого раздела.\n\n"
        "2. Требования к качеству\nТекст второго раздела."
    )
    chunks = split_tz_sections(text)
    # Чанки не пересекают границы разделов: ни один чанк не содержит оба раздела.
    for chunk in chunks:
        assert not (("Текст первого раздела" in chunk) and ("Текст второго раздела" in chunk))


def test_long_section_split_by_paragraphs_keeps_heading() -> None:
    heading = "1. Требования к оказанию услуг"
    paragraphs = [f"Параграф номер {i} " + "текст " * 60 for i in range(12)]
    text = heading + "\n\n" + "\n\n".join(paragraphs)
    chunks = split_tz_sections(text, max_chars=500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.startswith(heading)
        assert len(chunk) <= 500 + len(heading) + 2


def test_no_headings_falls_back_to_paragraphs() -> None:
    # Абзацы из коротких строк: чанки гарантированно укладываются в max_chars.
    text = "\n\n".join(
        f"Абзац {i}:\n" + "\n".join("слова и термины " * 6 for _ in range(6)) for i in range(10)
    )
    chunks = split_tz_sections(text, max_chars=300)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)
    assert all(len(c) <= 300 for c in chunks)


def test_empty_text() -> None:
    assert split_tz_sections("") == []
    assert split_tz_sections("   \n ") == []
