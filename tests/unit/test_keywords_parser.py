"""Тесты парсера файла ключевых слов/компетенций профиля (R8)."""

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

# Формат агрегаторов (ТендерПлан/ТендерЛэнд): англ. секции + компетенции.
SAMPLE_AGG = """
**name**
bbk-it

**keywords**
услуг* программирован*,
разработ* ИИ,

**exclussion_words**
радиопрограмм*,
(правов* систем*)~1,

**competencies**
Поставщик — BBK IT.
Основные компетенции: ИИ, автоматизация.
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


def test_parse_aggregator_format_with_competencies() -> None:
    parsed = parse_keywords_text(SAMPLE_AGG)
    assert parsed["name"] == "bbk-it"
    assert "услуг* программирован*" in parsed["keywords"]
    assert "разработ* ИИ" in parsed["keywords"]
    assert "(правов* систем*)~1" in parsed["exclusion_words"]
    assert "радиопрограмм*" in parsed["exclusion_words"]
    # Текст компетенций сохраняется как блок.
    assert "BBK IT" in parsed["competencies"]
    assert "автоматизация" in parsed["competencies"]


def test_parse_keywords_deduplicates() -> None:
    parsed = parse_keywords_text("**Ключевые слова**\nИИ,\nИИ,\n\n**Минус слова**\nремонт,\nремонт")
    assert parsed["keywords"] == ["ИИ"]
    assert parsed["exclusion_words"] == ["ремонт"]


def test_parse_keywords_empty() -> None:
    parsed = parse_keywords_text("нет секций")
    assert parsed == {
        "name": "",
        "keywords": [],
        "exclusion_words": [],
        "competencies": "",
    }


def test_parse_real_profile_file() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "data" / "profile.md"
    assert path.is_file()
    parsed = parse_keywords_file(path)
    # Новый формат profile.md: имя профиля + компетенции-ссылка на файл.
    assert parsed["name"] == "bbk-it"
    assert parsed["keywords"]
    assert parsed["exclusion_words"]
    # Компетенции подставлены из docs/references/bbk-it-site.md (ссылка в файле).
    assert "BBK IT" in parsed["competencies"]
    # Формы из файла: усечения слов и близость (…~N).
    assert any("*" in w for w in parsed["keywords"])
    assert any("~" in w for w in parsed["exclusion_words"])


def test_default_keywords_seed() -> None:
    seed = default_keywords_seed()
    assert seed["name"] == "bbk-it"
    assert seed["is_active"] is True
    assert isinstance(seed["keywords"], list)
    assert isinstance(seed["exclusion_words"], list)
    assert "BBK IT" in seed["competencies"]
