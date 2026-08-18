"""Промпты и примеры для fit- и judge-цепочек.

Подходы:
- **few-shot** — позитивные примеры «описание ↔ компетенции → эталон reasoning + fit_score»;
- **negative-example** — примеры, где термины совпадают, но смысл разный (false-friend),
  и где релевантность достигается синонимичными терминами при другой лексике.

Тексты промптов вынесены в markdown-файлы, few-shot примеры — в JSON (подпапка
``prompts`` рядом с модулем). Как и конфигурация сервиса, промпты редактируются
через web-интерфейс и применяются при следующем старте: тексты загружаются один
раз при импорте модуля. Каталог можно переопределить переменной окружения
``SCORE_PROMPTS_DIR`` (в Docker — общий том, куда пишет web-интерфейс).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

_PROMPTS_DIR = (
    Path(os.environ["SCORE_PROMPTS_DIR"])
    if os.environ.get("SCORE_PROMPTS_DIR")
    else Path(__file__).parent / "prompts"
)


def _load_md(name: str) -> str:
    """Текст промпта из markdown-файла (без ведущего перевода строки)."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").lstrip("\n")


SYSTEM_PROMPT_FIT = _load_md("fit_system.md")
SYSTEM_PROMPT_JUDGE = _load_md("judge_system.md")
_TRUNCATED_NOTE = _load_md("truncated_note.md")
_FULL_TEXT_NOTE = _load_md("full_text_note.md")


def _ex(example: dict[str, Any]) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Few-shot пример из JSON: человеческий вход + эталонный ответ (AIMessage)."""
    tag = "NEGATIVE" if example["negative"] else "POSITIVE"
    human = f"[{tag}] ОПИСАНИЕ ЗАКУПКИ:\n{example['description']}"
    reasoning_json = "{" + ", ".join(f'"{k}": "{v}"' for k, v in example["reasoning"].items()) + "}"
    ai = (
        '{"reasoning": '
        + reasoning_json
        + f', "fit_score": {example["fit_score"]}, "requires_tz_review": '
        + ("true" if example["requires_tz_review"] else "false")
        + ', "requires_tz_body": '
        + ("true" if example["requires_tz_body"] else "false")
        + "}"
    )
    return [HumanMessage(content=human)], [AIMessage(content=ai)]


def _load_examples(name: str) -> list[tuple[list[BaseMessage], list[BaseMessage]]]:
    """Few-shot примеры из JSON-файла (см. ``_ex``)."""
    data = json.loads((_PROMPTS_DIR / name).read_text(encoding="utf-8"))
    examples: list[dict[str, Any]] = data
    return [_ex(example) for example in examples]


FEW_SHOT: list[tuple[list[BaseMessage], list[BaseMessage]]] = _load_examples("few_shot.json")


def build_fit_messages(
    competencies: str,
    description: str,
    truncated: bool = False,
    full_text: bool = False,
) -> list[BaseMessage]:
    """Составить сообщения для fit-цепочки (система + few-shot + текущий вход).

    ``truncated=True`` — описание закупки обрезано многоточием: добавляется явное
    указание на неполноту описания (правило 7 системного промпта).
    ``full_text=True`` — уже предоставлен полный текст ТЗ (файл прочитан): модель
    не должна снова запрашивать чтение ТЗ (requires_tz_review/requires_tz_body).
    """
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT_FIT)]
    if truncated:
        messages.append(SystemMessage(content=_TRUNCATED_NOTE))
    if full_text:
        messages.append(SystemMessage(content=_FULL_TEXT_NOTE))
    for human, ai in FEW_SHOT:
        messages.extend(human)
        messages.extend(ai)
    messages.append(
        HumanMessage(
            content=f"КОМПЕТЕНЦИИ ПОСТАВЩИКА:\n{competencies}\n\nОПИСАНИЕ ЗАКУПКИ:\n{description}"
        )
    )
    return messages


def build_judge_messages(
    competencies: str,
    description: str,
    fit_result: str,
) -> list[BaseMessage]:
    """Составить сообщения для judge-цепочки (отдельный контекст)."""
    return [
        SystemMessage(content=SYSTEM_PROMPT_JUDGE),
        HumanMessage(
            content=(
                f"КОМПЕТЕНЦИИ ПОСТАВЩИКА:\n{competencies}\n\n"
                f"ОПИСАНИЕ ЗАКУПКИ:\n{description}\n\n"
                f"ОЦЕНКА МОДЕЛИ (JSON):\n{fit_result}\n\n"
                "Оцени адекватность этой оценки."
            )
        ),
    ]
