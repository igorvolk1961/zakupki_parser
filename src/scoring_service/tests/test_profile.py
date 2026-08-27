"""Тесты структурированного профиля поставщика (строгое разделение слоёв)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scoring_service.profile import (
    Profile,
    load_profile,
    parse_legacy_markdown,
    profile_to_text,
    profile_to_texts,
    render_profile,
    render_profile_embedding,
)

LEGACY_MD = """# Компетенции поставщика

Поставщик — **BBK IT** (ООО «Юнити»), «внедрение AI в бизнес».

BBK IT — архитекторы бизнес-процессов: сначала проектирует, как должен работать бизнес
клиента, потом внедряет ИИ.

## Основные компетенции

1. **Аудит и обследование ИТ-ландшафта и бизнес-процессов** — изучение текущего состояния.
2. **Автоматизация и проектирование бизнес-процессов** — поиск узких мест, интеграция систем.

## НЕ входят в компетенции

- Поставка и монтаж оборудования.
- Сопровождение legacy-систем сторонних вендоров.
"""


def test_parse_legacy_markdown() -> None:
    profile = parse_legacy_markdown(LEGACY_MD)
    assert profile.name == "BBK IT (ООО «Юнити»)"
    assert "архитекторы бизнес-процессов" in profile.positioning
    assert len(profile.competencies) == 2
    assert profile.competencies[0].area == "Аудит и обследование ИТ-ландшафта и бизнес-процессов"
    assert profile.competencies[1].description == "поиск узких мест, интеграция систем."
    assert profile.exclusions == [
        "Поставка и монтаж оборудования.",
        "Сопровождение legacy-систем сторонних вендоров.",
    ]


def test_parse_legacy_markdown_preserves_text() -> None:
    """Legacy-разбор не теряет текст: хвост первой строки и нераспознанные секции."""
    md = """# Компетенции поставщика

Поставщик — **BBK IT** (ООО «Юнити»), «внедрение AI в бизнес».

## Основные компетенции

1. **Аудит** — изучение состояния.

## Не выполняем

- Поставка и монтаж оборудования.

## Опыт

10 лет на рынке
"""
    profile = parse_legacy_markdown(md)
    # Хвост первой строки преамбулы после названия сохранён в позиционировании.
    assert "внедрение AI в бизнес" in profile.positioning
    # Нераспознанные секции не отбрасываются — попадают в позиционирование.
    assert "Опыт: 10 лет на рынке" in profile.positioning
    assert "Не выполняем: Поставка и монтаж оборудования." in profile.positioning
    # Известные секции по-прежнему разбираются структурированно.
    assert len(profile.competencies) == 1
    assert profile.competencies[0].area == "Аудит"
    assert profile.exclusions == []  # секция «Не выполняем» — текст, не исключения


def test_render_profile_sections() -> None:
    profile = Profile(
        name="X",
        positioning="Делаем X",
        breadth="broad",
        competencies=[
            {"area": "Автоматизация", "description": "любых процессов", "examples": ["кейс1"]}
        ],
        exclusions=["Монтаж"],
    )
    text = render_profile(profile)
    assert "Поставщик: X" in text
    assert "Позиционирование: Делаем X" in text
    assert "Охват:" in text
    assert "Основные компетенции:" in text
    assert "- Автоматизация: любых процессов Примеры: кейс1" in text
    assert "НЕ входят в компетенции (исключения):" in text
    assert "- Монтаж" in text
    # Дефолтные параметры политики в текст не выводятся (не шумят и в эмбеддинге).
    assert "Параметры скоринга" not in text


def test_render_profile_narrow_breadth() -> None:
    profile = Profile(positioning="X", breadth="narrow")
    text = render_profile(profile)
    assert "Охват: узкий:" in text


def test_render_profile_policy_overrides() -> None:
    profile = Profile(
        positioning="X",
        scoring_policy={"uncovered_penalty": 3.0, "ambiguous_range": [5.0, 7.0]},
    )
    text = render_profile(profile)
    assert "Параметры скоринга" in text
    assert "3 балла" in text
    assert "5-7" in text


def test_profile_to_text_structured() -> None:
    data = {"name": "X", "positioning": "Y", "breadth": "narrow"}
    text = profile_to_text(data)
    assert "Поставщик: X" in text
    assert "Охват:" in text


def test_profile_to_text_plain_string_not_supported() -> None:
    """Свободный текст компетенций не поддерживается (легаси удалено, BR-07)."""
    assert profile_to_text("test competencies") == ""
    assert profile_to_text(None) == ""
    assert profile_to_text(123) == ""


def test_profile_to_text_legacy_markdown_not_supported() -> None:
    """Legacy-markdown компетенций не поддерживается: возвращается пустой текст."""
    assert profile_to_text(LEGACY_MD) == ""


def test_load_profile_yaml(tmp_path: Path) -> None:
    f = tmp_path / "profile.yaml"
    f.write_text("name: X\npositioning: Y\n", encoding="utf-8")
    assert load_profile(f).name == "X"


def test_load_profile_json(tmp_path: Path) -> None:
    f = tmp_path / "profile.json"
    f.write_text('{"name": "X", "positioning": "Y"}', encoding="utf-8")
    assert load_profile(f).positioning == "Y"


def test_load_profile_markdown(tmp_path: Path) -> None:
    f = tmp_path / "profile.md"
    f.write_text(LEGACY_MD, encoding="utf-8")
    assert load_profile(f).name == "BBK IT (ООО «Юнити»)"


def test_render_profile_embedding_excludes_noise() -> None:
    """Для векторной близости исключения и политика НЕ попадают в текст."""
    profile = Profile(
        name="X",
        positioning="Внедряем ИИ и автоматизируем процессы",
        breadth="narrow",
        competencies=[{"area": "Автоматизация", "description": "процессов", "examples": ["кейс1"]}],
        exclusions=["Поставка и монтаж оборудования", "Видеонаблюдение"],
        scoring_policy={"uncovered_penalty": 3.0},
    )
    llm = render_profile(profile)
    emb = render_profile_embedding(profile)
    # В LLM-тексте исключения и параметры есть…
    assert "НЕ входят в компетенции (исключения):" in llm
    assert "Поставка и монтаж оборудования" in llm
    assert "Параметры скоринга" in llm
    # …а в тексте для эмбеддинга их нет (только позитивные факты).
    assert "НЕ входят в компетенции" not in emb
    assert "Поставка и монтаж оборудования" not in emb
    assert "Видеонаблюдение" not in emb
    assert "Параметры скоринга" not in emb
    assert "Охват" not in emb
    assert "Автоматизация: процессов Примеры: кейс1" in emb
    assert "Внедряем ИИ и автоматизируем процессы" in emb


def test_profile_to_texts_structured_and_plain() -> None:
    data = {
        "name": "X",
        "positioning": "Y",
        "exclusions": ["Монтаж"],
        "breadth": "narrow",
    }
    texts = profile_to_texts(data)
    assert texts is not None
    assert "НЕ входят в компетенции (исключения):" in texts.llm
    assert "НЕ входят в компетенции" not in texts.embedding
    assert texts.llm != texts.embedding

    # Свободный текст компетенций не поддерживается (легаси удалено, BR-07).
    assert profile_to_texts("test competencies") is None
    assert profile_to_texts(None) is None
    assert profile_to_texts(123) is None


def test_profile_to_text_json_string() -> None:
    """JSON-строка схемы Profile нормализуется в текст (BR-07)."""
    data = {"name": "X", "positioning": "Y", "breadth": "narrow"}
    texts = profile_to_texts(json.dumps(data))
    assert texts is not None
    assert "Поставщик: X" in texts.llm
    assert profile_to_text(json.dumps(data)) != ""


def test_load_profile_invalid_yaml_raises(tmp_path: Path) -> None:
    f = tmp_path / "profile.yaml"
    f.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_profile(f)


def test_default_profile_file_renders() -> None:
    """Штатный профиль (data/profile.yaml) должен рендериться в канонический текст."""
    root = Path(__file__).resolve().parents[1]
    profile = load_profile(root / "data" / "profile.yaml")
    text = render_profile(profile)
    assert "Позиционирование:" in text
    assert "Основные компетенции:" in text
    assert "НЕ входят в компетенции (исключения):" in text
    assert len(profile.competencies) >= 5
