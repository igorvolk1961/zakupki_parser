"""Промпты RAG-верификации стоп-условий (analysis_service).

Тексты промптов вынесены в markdown-файлы (подпапка ``prompts`` рядом с модулем)
и загружаются один раз при импорте — как и конфигурация сервиса, промпты
редактируются через web-интерфейс и применяются при следующем старте.
Каталог можно переопределить переменной окружения ``ANALYSIS_PROMPTS_DIR``
(в Docker — общий том, куда пишет web-интерфейс).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_PROMPTS_DIR = (
    Path(os.environ["ANALYSIS_PROMPTS_DIR"])
    if os.environ.get("ANALYSIS_PROMPTS_DIR")
    else Path(__file__).parent / "prompts"
)


def _load_md(name: str) -> str:
    """Текст промпта из markdown-файла (без ведущего перевода строки)."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").lstrip("\n")


VERDICT_SYSTEM = _load_md("verdict_system.md")
VERDICT_USER_TEMPLATE = _load_md("verdict_user.md")
BATCH_SYSTEM = _load_md("batch_system.md")


def _substitute(template: str, values: dict[str, str]) -> str:
    """Однопроходная подстановка {placeholders}: фигурные скобки в тексте ТЗ
    не ломают шаблон, вставленные значения не пересканируются второй раз.
    """
    return re.sub(
        r"\{(\w+)\}",
        lambda m: values.get(m.group(1), m.group(0)),
        template,
    )


def build_verdict_messages(question: str, context: str) -> tuple[str, str]:
    """Системный и пользовательский промпты для вердикта по одному вопросу.

    ``context`` — конкатенация top-k фрагментов ТЗ. Подстановка — однопроходная
    (regex), чтобы фигурные скобки в тексте ТЗ не ломали шаблон и вставленные
    значения не пересканировались второй подстановкой.
    """
    user = _substitute(VERDICT_USER_TEMPLATE, {"question": question, "context": context})
    return VERDICT_SYSTEM, user


def build_batch_system_messages(context: str) -> tuple[str, str]:
    """Системный и пользовательский промпты для извлечения фактов по трём
    обязательным проверкам за один LLM-вызов.

    ``context`` — релевантные секции ТЗ (лексический ретривал по паттернам
    системных проверок). Профиль поставщика в промпт не попадает.
    """
    user = _substitute(BATCH_SYSTEM, {"context": context})
    return BATCH_SYSTEM, user
