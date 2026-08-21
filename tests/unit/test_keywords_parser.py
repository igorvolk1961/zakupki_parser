"""Тесты парсера ``data/key_words.md`` (R8)."""

from __future__ import annotations

from pathlib import Path

from zakupki_parser.storage.keywords_parser import (
    default_keywords_seed,
    parse_keywords_file,
    parse_keywords_text,
)

SAMPLE = """
**Ключевые слова**
услуг* программирован*,
разработ* интеграцион* решен*,
(автоматизир* систем* учет*)~2,
разработ* ИИ,
"точная фраза",

**Минус слова**
радиопрограмм*,
(справоч* систем*)~1,
"ремонт",
1С Документооборот,

**Пример тендера**
https://example.com/1
"""


def test_parse_keywords_sections() -> None:
    parsed = parse_keywords_text(SAMPLE)
    assert "разработ* ИИ" in parsed["keywords"]
    assert "услуг* программирован*" in parsed["keywords"]
    assert "(автоматизир* систем* учет*)~2" in parsed["keywords"]
    # Кавычки снимаются, синтаксис * и ~N сохраняется.
    assert "точная фраза" in parsed["keywords"]
    assert "ремонт" in parsed["exclusion_words"]
    assert "1С Документооборот" in parsed["exclusion_words"]
    assert "(справоч* систем*)~1" in parsed["exclusion_words"]
    # Секции «Пример тендера» и URL не попадают в слова.
    assert "https://example.com/1" not in parsed["keywords"]
    assert not any("пример" in w.casefold() for w in parsed["keywords"])


def test_parse_keywords_deduplicates() -> None:
    parsed = parse_keywords_text("**Ключевые слова**\nИИ,\nИИ,\n\n**Минус слова**\nремонт,\nремонт")
    assert parsed["keywords"] == ["ИИ"]
    assert parsed["exclusion_words"] == ["ремонт"]


def test_parse_keywords_empty() -> None:
    parsed = parse_keywords_text("нет секций")
    assert parsed == {"keywords": [], "exclusion_words": []}


def test_parse_real_keywords_file() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "data" / "key_words.md"
    assert path.is_file()
    parsed = parse_keywords_file(path)
    assert parsed["keywords"]
    assert parsed["exclusion_words"]
    # Формы из файла: усечения слов и близость (…~N).
    assert any("*" in w for w in parsed["keywords"])
    assert any("~" in w for w in parsed["exclusion_words"])


def test_default_keywords_seed() -> None:
    seed = default_keywords_seed()
    assert seed["name"] == "default"
    assert seed["is_active"] is True
    assert isinstance(seed["keywords"], list)
    assert isinstance(seed["exclusion_words"], list)
